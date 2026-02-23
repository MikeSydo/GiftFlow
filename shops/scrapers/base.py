import logging
import random
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import httpx

logger = logging.getLogger('shops.scrapers')

# Pool of User-Agent strings for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
]

REQUEST_TIMEOUT = 15  # seconds
MAX_RETRIES = 3
MIN_DELAY = 1.0
MAX_DELAY = 3.0


@dataclass
class ProductData:
    """Standardised product data returned by every scraper."""
    name: str
    url: str
    price: Decimal
    original_price: Optional[Decimal] = None
    in_stock: bool = True
    image_url: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    category_hint: Optional[str] = None


class BaseShopScraper:
    """Abstract base for all shop-specific scrapers."""

    shop_slug: str = ''
    base_url: str = ''

    def __init__(self):
        self._client: Optional[httpx.Client] = None

    # Public contract — subclasses MUST override
    def get_category_urls(self) -> list[str]:
        """Return a list of catalogue page URLs to crawl."""
        raise NotImplementedError

    def parse_product_list(self, html: str, url: str) -> list[ProductData]:
        """Parse a catalogue/listing page → list of ProductData."""
        raise NotImplementedError

    def parse_product_detail(self, html: str, url: str) -> ProductData:
        """Parse a single product page → full ProductData."""
        raise NotImplementedError

    # Shared helpers
    @staticmethod
    def random_user_agent() -> str:
        return random.choice(USER_AGENTS)

    @staticmethod
    def random_delay():
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    def get_client(self) -> httpx.Client:
        """Lazily create an httpx client with sensible defaults."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={'User-Agent': self.random_user_agent()},
            )
        return self._client

    def fetch(self, url: str) -> str:
        """Fetch URL with retry + exponential back-off. Returns HTML text."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.random_delay()
                client = self.get_client()
                # rotate UA on every request
                client.headers['User-Agent'] = self.random_user_agent()
                response = client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                wait = 2 ** attempt + random.random()
                logger.warning(
                    '[%s] attempt %d failed for %s: %s — retrying in %.1fs',
                    self.shop_slug, attempt, url, exc, wait,
                )
                time.sleep(wait)
        logger.error('[%s] all %d attempts failed for %s', self.shop_slug, MAX_RETRIES, url)
        raise last_exc  # type: ignore[misc]

    def build_affiliate_url(self, url: str, affiliate_parameter: str) -> str:
        """Append affiliate parameter to the URL query string."""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        # affiliate_parameter expected as "key=value"
        if '=' in affiliate_parameter:
            key, value = affiliate_parameter.split('=', 1)
            query[key] = [value]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
