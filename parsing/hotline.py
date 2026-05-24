from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from django.conf import settings

logger = logging.getLogger("parsing.hotline")

HOTLINE_BASE_URL = "https://hotline.ua"
HOTLINE_SEARCH_URL = f"{HOTLINE_BASE_URL}/ua/sr/"
HOTLINE_JSON_RPC_SEARCH_URL = f"{HOTLINE_BASE_URL}/svc/search/api/json-rpc"
HOTLINE_FRONTEND_GRAPHQL_URL = f"{HOTLINE_BASE_URL}/svc/frontend-api/graphql"
HOTLINE_CATEGORY_MAX_PAGES = 50
HOTLINE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class HotlineChallengeError(RuntimeError):
    pass


@dataclass(frozen=True)
class HotlineOffer:
    external_product_id: str
    external_offer_id: str
    title: str
    product_url: str
    image_url: str | None
    price: Decimal | None
    original_price: Decimal | None = None
    seller_name: str | None = None
    seller_url: str | None = None
    category_hint: str | None = None
    needs_product_refresh: bool = True


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
        try:
            response = self.client.get(HOTLINE_SEARCH_URL, params={"q": query})
            response.raise_for_status()
            return self._parse_search_html(response.text)
        except HotlineChallengeError:
            logger.info("[hotline] search HTML challenged; falling back to JSON-RPC suggestions")
            return self.search_suggestions(query)

    def search_category(self, category_url: str, *, max_pages: int = HOTLINE_CATEGORY_MAX_PAGES) -> list[HotlineOffer]:
        offers: list[HotlineOffer] = []
        seen_offer_ids: set[str] = set()

        for page in range(1, max_pages + 1):
            response = self.client.get(self._category_page_url(category_url, page))
            response.raise_for_status()
            page_offers = self._parse_search_html(response.text)
            new_count = 0
            for offer in page_offers:
                if offer.external_offer_id in seen_offer_ids:
                    continue
                seen_offer_ids.add(offer.external_offer_id)
                offers.append(offer)
                new_count += 1

            if not page_offers or new_count == 0:
                break

        logger.info("[hotline] parsed %d category offers from %s", len(offers), category_url)
        return offers

    def search_suggestions(self, query: str) -> list[HotlineOffer]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "search.search",
            "params": {
                "q": query,
                "lang": "uk",
                "section_id": None,
                "entity": "products",
            },
        }
        response = self.client.post(
            HOTLINE_JSON_RPC_SEARCH_URL,
            json=payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": HOTLINE_BASE_URL,
                "Referer": HOTLINE_SEARCH_URL,
            },
        )
        response.raise_for_status()
        return self._parse_search_suggestions(response.json())

    def enrich_product_prices(self, offers: list[HotlineOffer]) -> list[HotlineOffer]:
        enriched = []
        for offer in offers:
            if offer.price is not None:
                enriched.append(offer)
                continue
            price, original_price = self.fetch_product_price_range(offer.product_url)
            if price is None:
                enriched.append(offer)
                continue
            enriched.append(
                replace(
                    offer,
                    price=price,
                    original_price=original_price,
                )
            )
        return enriched

    def fetch_product_price_range(self, product_url: str) -> tuple[Decimal | None, Decimal | None]:
        try:
            response = self.client.get(product_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.info("[hotline] failed to fetch product price url=%s error=%s", product_url, exc)
            return None, None

        if self._is_challenge_html(response.text):
            logger.info("[hotline] product price page challenged url=%s", product_url)
            return None, None

        return self._parse_product_price_range(response.text)

    def fetch_product_offers(
        self,
        product_url: str,
        external_product_id: str,
    ) -> list[HotlineMerchantOffer]:
        try:
            response = self.client.get(product_url)
            response.raise_for_status()
            return self._parse_product_html(response.text, external_product_id=external_product_id)
        except HotlineChallengeError:
            product_path = self._extract_product_path(product_url)
            if not product_path:
                raise
            return self.fetch_product_offers_graphql(
                product_path=product_path,
                product_url=product_url,
                external_product_id=external_product_id,
            )

    def fetch_product_offers_graphql(
        self,
        *,
        product_path: str,
        product_url: str,
        external_product_id: str,
    ) -> list[HotlineMerchantOffer]:
        token = getattr(settings, "HOTLINE_REQUEST_TOKEN", "")
        if not token:
            raise HotlineChallengeError(
                "Hotline product offers require a valid request token after captcha/challenge.",
            )

        query = """
        query getOffers($path:String!,$cityId:Int!){
          byPathQueryProduct(path:$path,cityId:$cityId){
            id
            offers(first:1000){
              totalCount
              edges{
                node{
                  _id
                  conversionUrl
                  descriptionFull
                  descriptionShort
                  firmId
                  firmLogo
                  firmTitle
                  firmExtraInfo
                  price
                  delivery{deliveryMethods hasFreeDelivery isSameCity name countryCodeFirm}
                }
              }
            }
          }
        }
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": HOTLINE_BASE_URL,
            "Referer": product_url,
            "x-language": "uk",
            "x-referer": product_url,
            "x-token": token,
        }
        cookie_header = getattr(settings, "HOTLINE_COOKIE_HEADER", "")
        if cookie_header:
            headers["Cookie"] = cookie_header

        response = self.client.post(
            HOTLINE_FRONTEND_GRAPHQL_URL,
            json={
                "operationName": "getOffers",
                "query": query,
                "variables": {
                    "path": product_path,
                    "cityId": getattr(settings, "HOTLINE_CITY_ID", 188),
                },
            },
            headers=headers,
        )
        response.raise_for_status()
        return self._parse_product_offers_graphql(
            response.json(),
            external_product_id=external_product_id,
        )

    def _parse_search_html(self, html: str) -> list[HotlineOffer]:
        if self._is_challenge_html(html):
            raise HotlineChallengeError("Hotline returned a captcha/challenge page for search results.")

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
        if self._is_challenge_html(html):
            raise HotlineChallengeError("Hotline returned a captcha/challenge page for product offers.")

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

    def _parse_search_suggestions(self, payload: dict) -> list[HotlineOffer]:
        results = payload.get("result") or []
        offers: list[HotlineOffer] = []
        seen_offer_ids: set[str] = set()
        for item in results:
            if item.get("currentEntity") != "products":
                continue
            product_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not product_id or not title or not url:
                continue
            external_offer_id = f"hotline-product-{product_id}"
            if external_offer_id in seen_offer_ids:
                continue
            seen_offer_ids.add(external_offer_id)
            image_url = item.get("imagePath") or None
            price = self._decimal_from_mapping(item, "minPrice", "price", "lowPrice")
            original_price = self._decimal_from_mapping(item, "maxPrice", "oldPrice", "highPrice")
            if original_price is not None and price is not None and original_price <= price:
                original_price = None
            offers.append(
                HotlineOffer(
                    external_product_id=product_id,
                    external_offer_id=external_offer_id,
                    title=title,
                    product_url=self._make_absolute_url(url),
                    image_url=self._make_absolute_url(image_url) if image_url else None,
                    price=price,
                    original_price=original_price,
                    needs_product_refresh=False,
                )
            )

        logger.info("[hotline] parsed %d product suggestions from JSON-RPC search", len(offers))
        return offers

    def _parse_product_offers_graphql(
        self,
        payload: dict,
        *,
        external_product_id: str,
    ) -> list[HotlineMerchantOffer]:
        errors = payload.get("errors") or []
        if any(error.get("message") == "invalid-request-token" for error in errors):
            raise HotlineChallengeError(
                "Hotline product offers require a valid request token after captcha/challenge.",
            )

        product = (payload.get("data") or {}).get("byPathQueryProduct") or {}
        offers_payload = product.get("offers") or {}
        offers: list[HotlineMerchantOffer] = []
        seen_offer_ids: set[str] = set()
        for edge in offers_payload.get("edges") or []:
            node = edge.get("node") or {}
            offer = self._parse_merchant_offer_json(node, external_product_id=external_product_id)
            if offer is None or offer.external_offer_id in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.external_offer_id)
            offers.append(offer)

        logger.info(
            "[hotline] parsed %d merchant offers from GraphQL product=%s",
            len(offers),
            external_product_id,
        )
        return offers

    def _parse_product_price_range(self, html: str) -> tuple[Decimal | None, Decimal | None]:
        aggregate_offer_match = re.search(
            r'"offers"\s*:\s*\{[^{}]*"@type"\s*:\s*"AggregateOffer"[^{}]*\}',
            html,
            re.S,
        )
        if aggregate_offer_match:
            aggregate_offer = aggregate_offer_match.group(0)
            low_price = self._json_number_value(aggregate_offer, "lowPrice")
            high_price = self._json_number_value(aggregate_offer, "highPrice")
            if low_price is not None:
                original_price = high_price if high_price is not None and high_price > low_price else None
                return low_price, original_price

        product_match = re.search(
            r"\bid:(?P<id>\d+)[^{}]{0,2000}?minPrice:(?P<min>[a-zA-Z0-9_.]+)[^{}]{0,400}?maxPrice:(?P<max>[a-zA-Z0-9_.]+)",
            html,
            re.S,
        )
        if product_match:
            low_price = self._decimal_from_js_token(product_match.group("min"), html)
            high_price = self._decimal_from_js_token(product_match.group("max"), html)
            if low_price is not None:
                original_price = high_price if high_price is not None and high_price > low_price else None
                return low_price, original_price

        return None, None

    def _json_number_value(self, text: str, key: str) -> Decimal | None:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"?(\d+(?:\.\d+)?)"?', text)
        if not match:
            return None
        return Decimal(match.group(1))

    def _decimal_from_js_token(self, token: str, html: str) -> Decimal | None:
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return Decimal(token)

        assignment = re.search(rf"\b{re.escape(token)}\s*=\s*(\d+(?:\.\d+)?)\b", html)
        if assignment:
            return Decimal(assignment.group(1))
        return None

    def _decimal_from_mapping(self, mapping: dict, *keys: str) -> Decimal | None:
        for key in keys:
            value = mapping.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, dict):
                nested = self._decimal_from_mapping(value, "value", "amount", "price")
                if nested is not None:
                    return nested
            try:
                return Decimal(str(value))
            except Exception:
                continue
        return None

    def _parse_merchant_offer_json(
        self,
        node: dict,
        *,
        external_product_id: str,
    ) -> HotlineMerchantOffer | None:
        offer_id = str(node.get("_id") or "").strip()
        conversion_url = str(node.get("conversionUrl") or "").strip()
        price_raw = node.get("price")
        seller_name = str(node.get("firmTitle") or "").strip()
        if not offer_id or not conversion_url or price_raw in (None, "") or not seller_name:
            return None

        firm_extra_info = node.get("firmExtraInfo") or {}
        if not isinstance(firm_extra_info, dict):
            firm_extra_info = {}
        website = firm_extra_info.get("website") or ""
        logo_url = firm_extra_info.get("officialFirmImage") or node.get("firmLogo") or ""
        title = node.get("descriptionShort") or node.get("descriptionFull") or seller_name

        return HotlineMerchantOffer(
            external_product_id=external_product_id,
            external_offer_id=offer_id,
            title=str(title),
            product_url=self._make_absolute_url(conversion_url),
            price=Decimal(str(price_raw)),
            seller_name=seller_name,
            seller_external_id=str(node.get("firmId") or "") or None,
            seller_url=self._make_external_website_url(str(website)) if website else None,
            seller_logo_url=self._make_absolute_url(str(logo_url)) if logo_url else None,
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

    @staticmethod
    def _is_challenge_html(html: str) -> bool:
        lowered = html.lower()
        return (
            "captcha" in lowered
            and "productssearch:[]" in lowered
            and "page-placeholder" in lowered
        )

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

    @staticmethod
    def _category_page_url(category_url: str, page: int) -> str:
        parts = urlsplit(category_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if page <= 1:
            query.pop("p", None)
        else:
            query["p"] = str(page)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(sorted(query.items())),
                "",
            )
        )

    @staticmethod
    def _extract_product_path(product_url: str) -> str:
        path = urlsplit(product_url).path.strip("/")
        if path.startswith("ua/"):
            path = path[3:]
        return path
