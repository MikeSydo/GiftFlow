import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from .base import BaseShopScraper, ProductData

logger = logging.getLogger('shops.scrapers')


class RozetkaSpider(BaseShopScraper):
    """
    Scraper for rozetka.ua.
    Uses Playwright for JS-rendered pages.
    """

    shop_slug = 'rozetka'
    base_url = 'https://rozetka.com.ua'

    # Category catalogue URLs to crawl
    CATEGORY_URLS = [
        'https://rozetka.com.ua/ua/computers-notebooks/c80253/'
    ]

    def get_category_urls(self) -> list[str]:
        return self.CATEGORY_URLS

    def _parse_price(self, text: str) -> Optional[Decimal]:
        """Extract numeric price from a string."""
        if not text:
            return None
        cleaned = re.sub(r'[^\d.,]', '', text.replace('\xa0', ''))
        cleaned = cleaned.replace(',', '.')
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    def parse_product_list(self, html: str, url: str) -> list[ProductData]:
        """
        Parse Rozetka catalogue page rendered by Playwright.
        Expected structure: <div class="goods-tile"> blocks.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error('[rozetka] beautifulsoup4 not installed')
            return []

        soup = BeautifulSoup(html, 'html.parser')
        products: list[ProductData] = []

        tiles = soup.select('.goods-tile')
        for tile in tiles:
            try:
                # Product name
                title_el = tile.select_one('.goods-tile__title')
                name = title_el.get_text(strip=True) if title_el else ''

                # Product URL
                link_el = tile.select_one('a.goods-tile__heading')
                product_url = link_el.get('href', '') if link_el else ''
                if product_url and not product_url.startswith('http'):
                    product_url = self.base_url + product_url

                # Price
                price_el = tile.select_one('.goods-tile__price-value')
                price = self._parse_price(price_el.get_text() if price_el else '')
                if price is None:
                    continue

                # Old price
                old_price_el = tile.select_one('.goods-tile__price--old .goods-tile__price-value')
                original_price = self._parse_price(old_price_el.get_text() if old_price_el else '')

                # In stock
                unavailable_el = tile.select_one('.goods-tile__availability--none')
                in_stock = unavailable_el is None

                # Image
                img_el = tile.select_one('img.goods-tile__picture')
                image_url = img_el.get('src', '') if img_el else None

                if name and product_url:
                    products.append(ProductData(
                        name=name,
                        url=product_url,
                        price=price,
                        original_price=original_price,
                        in_stock=in_stock,
                        image_url=image_url,
                    ))
            except Exception as exc:
                logger.warning('[rozetka] failed to parse tile: %s', exc)
                continue

        logger.info('[rozetka] parsed %d products from %s', len(products), url)
        return products

    def parse_product_detail(self, html: str, url: str) -> ProductData:
        """Parse single product page from Rozetka."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')

        title_el = soup.select_one('h1.product__title')
        name = title_el.get_text(strip=True) if title_el else ''

        price_el = soup.select_one('.product-prices__big .product-prices__big')
        price = self._parse_price(price_el.get_text() if price_el else '') or Decimal('0')

        old_price_el = soup.select_one('.product-prices__small')
        original_price = self._parse_price(old_price_el.get_text() if old_price_el else '')

        unavailable = soup.select_one('.status-label--unavailable')
        in_stock = unavailable is None

        img_el = soup.select_one('img.product-photo__picture')
        image_url = img_el.get('src', '') if img_el else None

        sku_el = soup.select_one('.product__code')
        sku = sku_el.get_text(strip=True).replace('Код:', '').strip() if sku_el else None

        desc_el = soup.select_one('.product-about__description-content')
        description = desc_el.get_text(strip=True) if desc_el else None

        return ProductData(
            name=name,
            url=url,
            price=price,
            original_price=original_price,
            in_stock=in_stock,
            image_url=image_url,
            sku=sku,
            description=description,
        )

    def fetch(self, url: str) -> str:
        """Override fetch to use Playwright for JS rendering."""
        import random
        import time
        from .base import MAX_RETRIES, USER_AGENTS

        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.random_delay()
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(
                        user_agent=random.choice(USER_AGENTS),
                        locale='uk-UA',
                    )
                    page = context.new_page()
                    page.goto(url, timeout=15000, wait_until='networkidle')
                    content = page.content()
                    browser.close()
                    return content
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt + random.random()
                logger.warning(
                    '[rozetka] playwright attempt %d failed for %s: %s — retrying in %.1fs',
                    attempt, url, exc, wait,
                )
                time.sleep(wait)

        logger.error('[rozetka] all %d attempts failed for %s', MAX_RETRIES, url)
        raise last_exc  # type: ignore[misc]
