from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger("parsing.hotline")

HOTLINE_BASE_URL = "https://hotline.ua"
HOTLINE_SEARCH_URL = f"{HOTLINE_BASE_URL}/ua/sr/"
HOTLINE_JSON_RPC_SEARCH_URL = f"{HOTLINE_BASE_URL}/svc/search/api/json-rpc"
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
    title: str
    product_url: str
    image_url: str | None
    category_hint: str | None = None
    can_cache_image: bool = True


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
        seen_product_ids: set[str] = set()

        for page in range(1, max_pages + 1):
            response = self.client.get(self._category_page_url(category_url, page))
            response.raise_for_status()
            page_offers = self._parse_search_html(response.text)
            new_count = 0
            for offer in page_offers:
                if offer.external_product_id in seen_product_ids:
                    continue
                seen_product_ids.add(offer.external_product_id)
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
        seen_product_ids: set[str] = set()
        for chunk in self._iter_object_chunks(products_segment):
            offer = self._parse_product_chunk(chunk)
            if offer is None or offer.external_product_id in seen_product_ids:
                continue
            seen_product_ids.add(offer.external_product_id)
            offers.append(offer)

        logger.info("[hotline] parsed %d offers from search payload", len(offers))
        return offers

    def _parse_product_chunk(self, chunk: str) -> HotlineOffer | None:
        product_id = self._match(r"_id:(\d+)", chunk)
        title = self._match_string(r'title:"((?:\\.|[^"])*)"', chunk)
        url = self._match_string(r'url:"((?:\\.|[^"])*)"', chunk)
        image_url = self._extract_image_url(chunk)

        if not product_id or not title or not url:
            return None

        return HotlineOffer(
            external_product_id=product_id,
            title=title,
            product_url=self._make_absolute_url(url),
            image_url=self._make_absolute_url(image_url) if image_url else None,
        )

    def _parse_search_suggestions(self, payload: dict) -> list[HotlineOffer]:
        results = payload.get("result") or []
        offers: list[HotlineOffer] = []
        seen_product_ids: set[str] = set()
        for item in results:
            if item.get("currentEntity") != "products":
                continue
            product_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not product_id or not title or not url:
                continue
            if product_id in seen_product_ids:
                continue
            seen_product_ids.add(product_id)
            image_url = item.get("imagePath") or None
            offers.append(
                HotlineOffer(
                    external_product_id=product_id,
                    title=title,
                    product_url=self._make_absolute_url(url),
                    image_url=self._make_absolute_url(image_url) if image_url else None,
                    can_cache_image=False,
                )
            )

        logger.info("[hotline] parsed %d product suggestions from JSON-RPC search", len(offers))
        return offers

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
