from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

import httpx

logger = logging.getLogger("search.hotline")

HOTLINE_BASE_URL = "https://hotline.ua"
HOTLINE_SEARCH_URL = f"{HOTLINE_BASE_URL}/ua/sr/"
HOTLINE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class HotlineOffer:
    external_product_id: str
    external_offer_id: str
    title: str
    product_url: str
    image_url: str | None
    price: Decimal
    original_price: Decimal | None = None
    seller_name: str | None = None
    seller_url: str | None = None
    category_hint: str | None = None


@dataclass(frozen=True)
class HotlineMerchantOffer:
    external_product_id: str
    external_offer_id: str
    title: str
    product_url: str
    price: Decimal
    original_price: Decimal | None = None
    seller_name: str | None = None
    seller_external_id: str | None = None
    seller_url: str | None = None
    image_url: str | None = None
    seller_logo_url: str | None = None


class HotlineAdapter:
    def __init__(self, client: httpx.Client | None = None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": HOTLINE_USER_AGENT},
        )

    def close(self) -> None:
        if self._owns_client and not self.client.is_closed:
            self.client.close()

    def __enter__(self) -> "HotlineAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def search(self, query: str, filters: dict | None = None) -> list[HotlineOffer]:
        response = self.client.get(HOTLINE_SEARCH_URL, params={"q": query})
        response.raise_for_status()
        return self._parse_search_html(response.text)

    def fetch_product_offers(
        self,
        product_url: str,
        external_product_id: str,
    ) -> list[HotlineMerchantOffer]:
        response = self.client.get(product_url)
        response.raise_for_status()
        return self._parse_product_html(response.text, external_product_id=external_product_id)

    def _parse_search_html(self, html: str) -> list[HotlineOffer]:
        nuxt_payload = self._extract_nuxt_payload(html)
        if not nuxt_payload:
            logger.warning("[hotline] no __NUXT__ payload found in search HTML")
            return []

        products_segment = self._extract_balanced_segment(
            nuxt_payload,
            marker="productsSearch:",
        )
        if not products_segment:
            logger.info("[hotline] productsSearch segment is missing")
            return []

        offers: list[HotlineOffer] = []
        seen_offer_ids: set[str] = set()
        for chunk in self._iter_object_chunks(products_segment):
            offer = self._parse_product_chunk(chunk)
            if offer is None or offer.external_offer_id in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.external_offer_id)
            offers.append(offer)

        logger.info("[hotline] parsed %d offers from search payload", len(offers))
        return offers

    def _parse_product_html(
        self,
        html: str,
        *,
        external_product_id: str,
    ) -> list[HotlineMerchantOffer]:
        nuxt_payload = self._extract_nuxt_payload(html)
        if not nuxt_payload:
            logger.warning("[hotline] no __NUXT__ payload found in product HTML")
            return []

        offers_segment = self._extract_balanced_segment(nuxt_payload, marker="offers:")
        if not offers_segment:
            logger.info("[hotline] offers segment is missing for product=%s", external_product_id)
            return []

        edges_segment = self._extract_balanced_segment(offers_segment, marker="edges:")
        if not edges_segment:
            logger.info("[hotline] offer edges segment is missing for product=%s", external_product_id)
            return []

        sales_segment = self._extract_balanced_segment(nuxt_payload, marker="sales:{sales:")
        old_price_map = self._extract_old_price_map(sales_segment or "")

        offers: list[HotlineMerchantOffer] = []
        seen_offer_ids: set[str] = set()
        for edge_chunk in self._iter_object_chunks(edges_segment):
            node_chunk = self._extract_balanced_segment(edge_chunk, marker="node:")
            if not node_chunk:
                continue
            offer = self._parse_merchant_offer_chunk(
                node_chunk,
                external_product_id=external_product_id,
                old_price_map=old_price_map,
            )
            if offer is None or offer.external_offer_id in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.external_offer_id)
            offers.append(offer)

        logger.info(
            "[hotline] parsed %d merchant offers from product=%s",
            len(offers),
            external_product_id,
        )
        return offers

    def _parse_product_chunk(self, chunk: str) -> HotlineOffer | None:
        product_id = self._match(r"_id:(\d+)", chunk)
        title = self._match_string(r'title:"((?:\\.|[^"])*)"', chunk)
        url = self._match_string(r'url:"((?:\\.|[^"])*)"', chunk)
        price_raw = self._match(r"minPrice:(\d+(?:\.\d+)?)", chunk)
        max_price_raw = self._match(r"maxPrice:(\d+(?:\.\d+)?)", chunk)
        image_url = self._extract_image_url(chunk)

        if not product_id or not title or not url or not price_raw:
            return None

        price = Decimal(price_raw)
        original_price = Decimal(max_price_raw) if max_price_raw else None
        if original_price is not None and original_price <= price:
            original_price = None

        return HotlineOffer(
            external_product_id=product_id,
            external_offer_id=f"hotline-product-{product_id}",
            title=title,
            product_url=self._make_absolute_url(url),
            image_url=self._make_absolute_url(image_url) if image_url else None,
            price=price,
            original_price=original_price,
        )

    def _parse_merchant_offer_chunk(
        self,
        chunk: str,
        *,
        external_product_id: str,
        old_price_map: dict[str, Decimal],
    ) -> HotlineMerchantOffer | None:
        offer_id = self._match_string(r'_id:"((?:\\.|[^"])*)"', chunk)
        conversion_url = self._match_string(r'conversionUrl:"((?:\\.|[^"])*)"', chunk)
        title = (
            self._match_string(r'descriptionShort:"((?:\\.|[^"])*)"', chunk)
            or self._match_string(r'descriptionFull:"((?:\\.|[^"])*)"', chunk)
            or ""
        )
        price_raw = self._match(r"price:(\d+(?:\.\d+)?)", chunk)
        firm_id = self._match(r"firmId:(\d+)", chunk)
        seller_name = self._match_string(r'firmTitle:"((?:\\.|[^"])*)"', chunk)
        website = self._match_string(r'website:"((?:\\.|[^"])*)"', chunk)
        logo_url = self._match_string(r'officialFirmImage:"((?:\\.|[^"])*)"', chunk)

        if not offer_id or not conversion_url or not price_raw or not seller_name:
            return None

        price = Decimal(price_raw)
        original_price = old_price_map.get(offer_id)
        if original_price is not None and original_price <= price:
            original_price = None

        return HotlineMerchantOffer(
            external_product_id=external_product_id,
            external_offer_id=offer_id,
            title=title or seller_name,
            product_url=self._make_absolute_url(conversion_url),
            price=price,
            original_price=original_price,
            seller_name=seller_name,
            seller_external_id=firm_id,
            seller_url=self._make_external_website_url(website) if website else None,
            seller_logo_url=self._make_absolute_url(logo_url) if logo_url else None,
        )

    def _extract_image_url(self, chunk: str) -> str | None:
        image_links_match = re.search(r"imageLinks:\[(.*?)\]", chunk, re.S)
        if image_links_match is None:
            return None

        image_match = re.search(
            r'(?:big|basic|small|thumb):"((?:\\.|[^"])*)"',
            image_links_match.group(1),
        )
        if image_match is None:
            return None
        return self._decode_js_string(image_match.group(1))

    @staticmethod
    def _extract_nuxt_payload(html: str) -> str | None:
        match = re.search(r"window\.__NUXT__=(.*?)(?:</script>|$)", html, re.S)
        if match is None:
            return None
        return match.group(1)

    @classmethod
    def _extract_balanced_segment(cls, text: str, marker: str) -> str | None:
        marker_index = text.find(marker)
        if marker_index == -1:
            return None

        start_index = marker_index + len(marker)
        while start_index < len(text) and text[start_index].isspace():
            start_index += 1

        if start_index >= len(text) or text[start_index] not in "[{":
            return None

        return cls._scan_balanced(text, start_index)

    @classmethod
    def _scan_balanced(cls, text: str, start_index: int) -> str | None:
        opening = text[start_index]
        closing = "]" if opening == "[" else "}"
        depth = 0
        in_string = False
        escaping = False

        for index in range(start_index, len(text)):
            char = text[index]
            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == opening:
                depth += 1
                continue
            if char == closing:
                depth -= 1
                if depth == 0:
                    return text[start_index:index + 1]

        return None

    @classmethod
    def _iter_object_chunks(cls, array_chunk: str) -> Iterator[str]:
        index = 1
        while index < len(array_chunk) - 1:
            char = array_chunk[index]
            if char == "{":
                chunk = cls._scan_balanced(array_chunk, index)
                if chunk is None:
                    return
                yield chunk
                index += len(chunk)
                continue
            index += 1

    def _extract_old_price_map(self, sales_segment: str) -> dict[str, Decimal]:
        if not sales_segment:
            return {}

        mapping: dict[str, Decimal] = {}
        for offer_id, old_price in re.findall(
            r'"(\d+)":\{.*?oldPrice:(\d+(?:\.\d+)?)',
            sales_segment,
            re.S,
        ):
            mapping[offer_id] = Decimal(old_price)
        return mapping

    @staticmethod
    def _match(pattern: str, text: str) -> str | None:
        match = re.search(pattern, text, re.S)
        if match is None:
            return None
        return match.group(1)

    def _match_string(self, pattern: str, text: str) -> str | None:
        value = self._match(pattern, text)
        if value is None:
            return None
        return self._decode_js_string(value)

    @staticmethod
    def _decode_js_string(value: str) -> str:
        return json.loads(f'"{value}"')

    @staticmethod
    def _make_absolute_url(value: str) -> str:
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if value.startswith("/"):
            return f"{HOTLINE_BASE_URL}{value}"
        return f"{HOTLINE_BASE_URL}/{value.lstrip('/')}"

    @staticmethod
    def _make_external_website_url(value: str) -> str:
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://{value.lstrip('/')}"
