import logging
import random
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Optional

from .base import BaseShopScraper, ProductData, MAX_RETRIES, USER_AGENTS

logger = logging.getLogger('shops.scrapers')


class RozetkaSpider(BaseShopScraper):
    """
    Scraper for rozetka.com.ua.

    Strategy:
    1. Open the category page with Playwright (bypasses Cloudflare)
    2. Extract product IDs from page links via page.evaluate()
    3. Call Rozetka's internal API via fetch() inside the browser context
       (browser already has valid Cloudflare tokens for all subdomains)
    4. Parse product data from the JSON response

    This avoids DOM scraping entirely — no BeautifulSoup, no CSS selectors,
    just clean JSON from the API.
    """

    shop_slug = 'rozetka'
    base_url = 'https://rozetka.com.ua'
    API_URL = 'https://common-api.rozetka.com.ua/v1/api/product/details'

    # Leaf category pages (pages that actually list products)
    CATEGORY_URLS = [
        'https://rozetka.com.ua/ua/notebooks/c80004/',
    ]

    # Maximum product IDs to fetch per API call
    API_BATCH_SIZE = 50

    def get_category_urls(self) -> list[str]:
        return self.CATEGORY_URLS

    def _parse_price(self, value) -> Optional[Decimal]:
        """Convert price value (int, float, or string) to Decimal."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)):
                return Decimal(str(value))
            cleaned = re.sub(r'[^\d.,]', '', str(value).replace('\xa0', ''))
            cleaned = cleaned.replace(',', '.')
            return Decimal(cleaned) if cleaned else None
        except (InvalidOperation, ValueError):
            return None

    def _category_hint_from_url(self, url: str) -> str:
        """Derive category hint from listing URL path."""
        from urllib.parse import urlparse
        raw_parts = urlparse(url).path.split('/')
        category_parts = [
            p for p in raw_parts
            if p and p != 'ua'
            and not (p.startswith('c') and p[1:].isdigit())
        ]
        return ' '.join(category_parts).replace('-', ' ')

    def fetch_and_parse(self, url: str) -> list[ProductData]:
        """
        Open the category page, extract product IDs, call the API
        from within the browser, and return parsed products.
        """
        category_hint = self._category_hint_from_url(url)
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.random_delay()
                return self._fetch_via_browser_api(url, category_hint)
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt + random.random()
                logger.warning(
                    '[rozetka] attempt %d failed for %s: %s — retrying in %.1fs',
                    attempt, url, exc, wait,
                )
                time.sleep(wait)

        logger.error('[rozetka] all %d attempts failed for %s', MAX_RETRIES, url)
        raise last_exc  # type: ignore[misc]

    def _fetch_via_browser_api(self, url: str, category_hint: str) -> list[ProductData]:
        """
        Open page with Playwright, extract product IDs from DOM,
        then call the product-details API from within the browser.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                locale='uk-UA',
                viewport={'width': 1280, 'height': 800},
            )
            page = context.new_page()
            page.goto(url, timeout=30000, wait_until='networkidle')

            # Wait for Angular to render product links
            page.wait_for_timeout(3000)

            # Step 1: Extract product IDs from page links
            product_ids = page.evaluate(r'''() => {
                const links = document.querySelectorAll('a[href*="/p"]');
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\/p(\d+)\//);
                    if (match && !ids.includes(match[1])) {
                        ids.push(match[1]);
                    }
                }
                return ids;
            }''')

            logger.info(
                '[rozetka] found %d product IDs from page links on %s',
                len(product_ids), url,
            )

            if not product_ids:
                browser.close()
                return []

            # Step 2: Call API in batches from within the browser
            all_raw_products = []

            for i in range(0, len(product_ids), self.API_BATCH_SIZE):
                batch = product_ids[i:i + self.API_BATCH_SIZE]
                ids_str = ','.join(batch)
                api_url = (
                    f'{self.API_URL}?country=UA&lang=ua&ids={ids_str}'
                )

                result = page.evaluate('''async (apiUrl) => {
                    try {
                        const resp = await fetch(apiUrl);
                        if (!resp.ok) return { error: 'HTTP ' + resp.status };
                        const json = await resp.json();
                        return { data: json.data || [] };
                    } catch(e) {
                        return { error: e.message };
                    }
                }''', api_url)

                if 'error' in result:
                    logger.warning(
                        '[rozetka] API batch %d failed: %s',
                        i // self.API_BATCH_SIZE + 1, result['error'],
                    )
                    continue

                batch_products = result.get('data', [])
                all_raw_products.extend(batch_products)
                logger.info(
                    '[rozetka] API batch %d: got %d products',
                    i // self.API_BATCH_SIZE + 1, len(batch_products),
                )

            browser.close()

        # Step 3: Convert raw API data to ProductData
        return self._parse_api_products(all_raw_products, category_hint)

    def _parse_api_products(
        self, raw_products: list[dict], category_hint: str,
    ) -> list[ProductData]:
        """Convert raw JSON product dicts from API to ProductData list."""
        seen_ids = set()
        products: list[ProductData] = []

        for item in raw_products:
            product_id = item.get('id')
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            title = item.get('title', '')
            href = item.get('href', '')
            price = self._parse_price(item.get('price'))
            old_price = self._parse_price(item.get('old_price'))

            if not title or not href or price is None:
                continue

            # Image URL
            image_url = None
            img = item.get('image_main') or item.get('images')
            if isinstance(img, str) and img:
                image_url = img
            elif isinstance(img, dict):
                image_url = img.get('url') or img.get('original') or img.get('large')
            elif isinstance(img, list) and img:
                first = img[0]
                image_url = first if isinstance(first, str) else first.get('url', '')

            # Stock status
            sell_status = item.get('sell_status', 'available')
            in_stock = sell_status not in (
                'ended', 'unavailable', 'waiting_for_supply',
            )

            products.append(ProductData(
                name=title,
                url=href,
                price=price,
                original_price=old_price if old_price and old_price != price else None,
                in_stock=in_stock,
                image_url=image_url,
                category_hint=category_hint,
            ))

        logger.info('[rozetka] parsed %d unique products', len(products))
        return products

    # Keep for base class compatibility
    def fetch(self, url: str) -> str:
        raise NotImplementedError('RozetkaSpider uses fetch_and_parse()')

    def parse_product_list(self, html: str, url: str) -> list[ProductData]:
        raise NotImplementedError('RozetkaSpider uses fetch_and_parse()')

    def parse_product_detail(self, html: str, url: str) -> ProductData:
        """Parse single product page (fallback, rarely used)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')

        title_el = soup.select_one('h1.product__title')
        name = title_el.get_text(strip=True) if title_el else ''

        price_el = soup.select_one('.product-prices__big .product-prices__big')
        price = self._parse_price(
            price_el.get_text() if price_el else '',
        ) or Decimal('0')

        old_price_el = soup.select_one('.product-prices__small')
        original_price = self._parse_price(
            old_price_el.get_text() if old_price_el else '',
        )

        unavailable = soup.select_one('.status-label--unavailable')
        in_stock = unavailable is None

        img_el = soup.select_one('img.product-photo__picture')
        image_url = img_el.get('src', '') if img_el else None

        sku_el = soup.select_one('.product__code')
        sku = (
            sku_el.get_text(strip=True).replace('Код:', '').strip()
            if sku_el else None
        )

        desc_el = soup.select_one('.product-about__description-content')
        description = desc_el.get_text(strip=True) if desc_el else None

        breadcrumb_els = soup.select('.breadcrumbs__item span')
        category_hint = ' > '.join(
            el.get_text(strip=True)
            for el in breadcrumb_els
            if el.get_text(strip=True)
        )

        return ProductData(
            name=name,
            url=url,
            price=price,
            original_price=original_price,
            in_stock=in_stock,
            image_url=image_url,
            sku=sku,
            description=description,
            category_hint=category_hint,
        )
