import logging
import random
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger("shops.connectors")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
MIN_DELAY = 1.0
MAX_DELAY = 3.0


@dataclass
class ProductOfferData:
    name: str
    url: str
    price: Decimal
    original_price: Optional[Decimal] = None
    in_stock: bool = True
    image_url: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    category_hint: Optional[str] = None
    external_offer_id: Optional[str] = None
    external_product_id: Optional[str] = None
    seller_name: Optional[str] = None
    seller_external_id: Optional[str] = None
    seller_url: Optional[str] = None
    is_marketplace_offer: bool = False


class BaseConnector:
    def __init__(self, integration):
        self.integration = integration
        self._client: Optional[httpx.Client] = None

    def discover_products(self, source) -> list[ProductOfferData]:
        raise NotImplementedError

    def fetch_product_detail(self, product_link) -> ProductOfferData:
        raise NotImplementedError

    @staticmethod
    def random_user_agent() -> str:
        if settings.SCRAPING_USER_AGENT:
            return settings.SCRAPING_USER_AGENT
        return random.choice(USER_AGENTS)

    @staticmethod
    def random_delay() -> None:
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    @staticmethod
    def parse_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            if isinstance(value, Decimal):
                return value
            if isinstance(value, (int, float)):
                return Decimal(str(value))
            cleaned = "".join(ch for ch in str(value).replace("\xa0", "") if ch.isdigit() or ch in ".,")
            cleaned = cleaned.replace(",", ".")
            return Decimal(cleaned) if cleaned else None
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        return normalized in {"1", "true", "yes", "y", "available", "in_stock", "in stock"}

    @staticmethod
    def resolve_path(payload: Any, path: str, default: Any = None) -> Any:
        if not path:
            return payload

        current = payload
        for raw_part in path.split("."):
            if raw_part == "":
                continue

            if isinstance(current, list):
                if not raw_part.isdigit():
                    return default
                index = int(raw_part)
                if index >= len(current):
                    return default
                current = current[index]
                continue

            if not isinstance(current, dict):
                return default

            current = current.get(raw_part, default)
            if current is default:
                return default

        return current

    @staticmethod
    def build_affiliate_url(url: str, affiliate_parameter: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "=" in affiliate_parameter:
            key, value = affiliate_parameter.split("=", 1)
            query[key] = [value]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def build_query_url(self, source) -> str:
        raw_value = (source.value or "").strip()
        if source.source_type != "seed_query":
            return raw_value

        raw_query = raw_value
        config = dict(self.integration.request_config or {})
        config.update(source.config or {})
        query_prefix = config.get("query_prefix", "")
        query_suffix = config.get("query_suffix", "")
        query = f"{query_prefix}{raw_query}{query_suffix}".strip()

        template = config.get("search_url_template")
        if template:
            return template.format(query=quote_plus(query), raw_query=query)

        query_param = config.get("query_param")
        if query_param:
            base_url = config.get("search_base_url") or self.integration.base_url
            parsed = urlparse(base_url)
            params = dict(parse_qs(parsed.query))
            params[query_param] = [query]
            return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

        return raw_query

    def get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            headers = {"User-Agent": self.random_user_agent()}
            headers.update(self.integration.request_config.get("headers", {}))
            self._client = httpx.Client(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            )
            self._apply_auth(self._client)
        return self._client

    def _apply_auth(self, client: httpx.Client) -> None:
        auth_type = self.integration.auth_type
        auth_config = self.integration.auth_config or {}

        if auth_type == "bearer_token" and auth_config.get("token"):
            client.headers["Authorization"] = f"Bearer {auth_config['token']}"
        elif auth_type == "api_key_header" and auth_config.get("key") and auth_config.get("value"):
            client.headers[auth_config["key"]] = auth_config["value"]
        elif auth_type == "basic" and auth_config.get("username") is not None:
            client.auth = (
                auth_config.get("username", ""),
                auth_config.get("password", ""),
            )

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.random_delay()
                client = self.get_client()
                client.headers["User-Agent"] = self.random_user_agent()
                request_kwargs = dict(kwargs)
                if self.integration.auth_type == "api_key_query":
                    params = dict(request_kwargs.get("params") or {})
                    auth_config = self.integration.auth_config or {}
                    if auth_config.get("key") and auth_config.get("value"):
                        params[auth_config["key"]] = auth_config["value"]
                    request_kwargs["params"] = params
                response = client.request(method, url, **request_kwargs)
                response.raise_for_status()
                return response
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                wait = 2 ** attempt + random.random()
                logger.warning(
                    "[%s] attempt %d failed for %s: %s; retrying in %.1fs",
                    self.integration.connector_type, attempt, url, exc, wait,
                )
                time.sleep(wait)

        logger.error("[%s] all %d attempts failed for %s", self.integration.connector_type, MAX_RETRIES, url)
        raise last_exc  # type: ignore[misc]

    def fetch_json(self, url: str, **kwargs) -> Any:
        return self._request("GET", url, **kwargs).json()

    def fetch_text(self, url: str, **kwargs) -> str:
        return self._request("GET", url, **kwargs).text

    def extract_html_field(self, node, config: Any) -> Optional[str]:
        if isinstance(config, str):
            found = node.select_one(config)
            return found.get_text(" ", strip=True) if found else None

        if not isinstance(config, dict):
            return None

        selector = config.get("selector")
        attr = config.get("attr")
        found = node.select_one(selector) if selector else node
        if found is None:
            return None
        if attr:
            return found.get(attr)
        return found.get_text(" ", strip=True)

    def parse_html_listing(
        self,
        html: str,
        item_selector: str,
        field_mapping: dict[str, Any],
        category_hint: Optional[str] = None,
        default_marketplace: bool = False,
    ) -> list[ProductOfferData]:
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(item_selector)
        products: list[ProductOfferData] = []

        for item in items:
            name = self.extract_html_field(item, field_mapping.get("title"))
            url = self.extract_html_field(item, field_mapping.get("url"))
            price = self.parse_decimal(self.extract_html_field(item, field_mapping.get("price")))
            if not name or not url or price is None:
                continue

            original_price = self.parse_decimal(
                self.extract_html_field(item, field_mapping.get("original_price"))
            )
            products.append(
                ProductOfferData(
                    name=name[:300],
                    url=url,
                    price=price,
                    original_price=original_price,
                    in_stock=self.as_bool(
                        self.extract_html_field(item, field_mapping.get("availability"))
                        or True
                    ),
                    image_url=self.extract_html_field(item, field_mapping.get("image")),
                    sku=self.extract_html_field(item, field_mapping.get("sku")),
                    category_hint=category_hint,
                    seller_name=self.extract_html_field(item, field_mapping.get("seller")),
                    seller_external_id=self.extract_html_field(item, field_mapping.get("seller_external_id")),
                    external_offer_id=self.extract_html_field(item, field_mapping.get("external_offer_id")),
                    external_product_id=self.extract_html_field(item, field_mapping.get("external_product_id")),
                    seller_url=self.extract_html_field(item, field_mapping.get("seller_url")),
                    is_marketplace_offer=default_marketplace,
                )
            )

        return products

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
