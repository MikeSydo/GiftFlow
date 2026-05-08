from __future__ import annotations

import random
import re
import time
from decimal import Decimal
from typing import Optional

from .base import MAX_RETRIES, USER_AGENTS, BaseConnector, ProductOfferData


class MarketplaceTemplateConnector(BaseConnector):
    def discover_products(self, source) -> list[ProductOfferData]:
        template = self.integration.request_config.get("template")
        if template == "rozetka":
            return self._discover_rozetka(self._resolve_discovery_url(source))
        raise ValueError(f"Unsupported marketplace template: {template}")

    def fetch_product_detail(self, product_link) -> ProductOfferData:
        template = self.integration.request_config.get("template")
        if template != "rozetka":
            raise ValueError(f"Unsupported marketplace template: {template}")

        html = self.fetch_text(product_link.product_url)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one("h1.product__title")
        name = title_el.get_text(strip=True) if title_el else product_link.product_name

        price_el = soup.select_one(".product-prices__big .product-prices__big")
        price = self.parse_decimal(price_el.get_text() if price_el else "") or product_link.price

        old_price_el = soup.select_one(".product-prices__small")
        original_price = self.parse_decimal(old_price_el.get_text() if old_price_el else "")

        unavailable = soup.select_one(".status-label--unavailable")
        in_stock = unavailable is None

        img_el = soup.select_one("img.product-photo__picture")
        image_url = img_el.get("src", "") if img_el else product_link.image_url

        sku_el = soup.select_one(".product__code")
        sku = (
            sku_el.get_text(strip=True).replace("Код:", "").strip()
            if sku_el else product_link.sku
        )

        breadcrumb_els = soup.select(".breadcrumbs__item span")
        category_hint = " > ".join(
            el.get_text(strip=True)
            for el in breadcrumb_els
            if el.get_text(strip=True)
        ) or product_link.original_category_name

        return ProductOfferData(
            name=name,
            url=product_link.product_url,
            price=price,
            original_price=original_price,
            in_stock=in_stock,
            image_url=image_url,
            sku=sku,
            category_hint=category_hint,
            external_offer_id=product_link.external_offer_id,
            external_product_id=product_link.external_product_id,
            seller_name=product_link.seller_name,
            seller_external_id=product_link.seller_external_id,
            seller_url=product_link.seller_url,
            is_marketplace_offer=True,
        )

    def _discover_rozetka(self, url: str) -> list[ProductOfferData]:
        category_hint = self._category_hint_from_url(url)
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.random_delay()
                return self._fetch_rozetka_via_browser_api(url, category_hint)
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt + random.random()
                time.sleep(wait)

        raise last_exc  # type: ignore[misc]

    def _resolve_discovery_url(self, source) -> str:
        return self.build_query_url(source)

    def _category_hint_from_url(self, url: str) -> str:
        from urllib.parse import urlparse

        raw_parts = urlparse(url).path.split("/")
        category_parts = [
            part for part in raw_parts
            if part and part != "ua" and not (part.startswith("c") and part[1:].isdigit())
        ]
        return " ".join(category_parts).replace("-", " ")

    def _fetch_rozetka_via_browser_api(self, url: str, category_hint: str) -> list[ProductOfferData]:
        from playwright.sync_api import sync_playwright

        api_url = self.integration.request_config.get(
            "api_url",
            "https://common-api.rozetka.com.ua/v1/api/product/details",
        )
        api_batch_size = int(self.integration.request_config.get("api_batch_size", 50))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                locale="uk-UA",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)
            product_ids = page.evaluate(r"""() => {
                const links = document.querySelectorAll('a[href*="/p"]');
                const ids = [];
                for (const link of links) {
                    const match = link.href.match(/\/p(\d+)\//);
                    if (match && !ids.includes(match[1])) {
                        ids.push(match[1]);
                    }
                }
                return ids;
            }""")

            all_products = []
            for index in range(0, len(product_ids), api_batch_size):
                batch = product_ids[index:index + api_batch_size]
                ids_str = ",".join(batch)
                batch_url = f"{api_url}?country=UA&lang=ua&ids={ids_str}"
                result = page.evaluate(
                    """async (requestUrl) => {
                        try {
                            const response = await fetch(requestUrl);
                            if (!response.ok) {
                                return { error: 'HTTP ' + response.status };
                            }
                            const json = await response.json();
                            return { data: json.data || [] };
                        } catch (error) {
                            return { error: error.message };
                        }
                    }""",
                    batch_url,
                )
                if "error" in result:
                    continue
                all_products.extend(result.get("data", []))

            browser.close()

        return self._parse_rozetka_products(all_products, category_hint)

    def _parse_rozetka_products(self, raw_products: list[dict], category_hint: str) -> list[ProductOfferData]:
        seen_ids = set()
        products: list[ProductOfferData] = []

        for item in raw_products:
            product_id = item.get("id")
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            title = item.get("title", "")
            href = item.get("href", "")
            price = self.parse_decimal(item.get("price"))
            old_price = self.parse_decimal(item.get("old_price"))
            if not title or not href or price is None:
                continue

            image_url = None
            images_data = item.get("images")
            if isinstance(images_data, dict):
                image_url = images_data.get("main") or images_data.get("original")
            elif isinstance(images_data, list) and images_data:
                first = images_data[0]
                image_url = first if isinstance(first, str) else first.get("url")
            if not image_url and isinstance(item.get("image_main"), str):
                image_url = item.get("image_main")

            sell_status = item.get("sell_status", "available")
            in_stock = sell_status not in {"ended", "unavailable", "waiting_for_supply"}
            seller_name = None
            seller_external_id = None
            seller_url = None

            seller = item.get("seller") if isinstance(item, dict) else None
            if isinstance(seller, dict):
                seller_name = seller.get("title") or seller.get("name")
                seller_external_id = str(seller.get("id")) if seller.get("id") is not None else None
                seller_url = seller.get("href") or seller.get("url")

            products.append(
                ProductOfferData(
                    name=title,
                    url=href,
                    price=price,
                    original_price=old_price if old_price and old_price != price else None,
                    in_stock=in_stock,
                    image_url=image_url,
                    category_hint=category_hint,
                    external_offer_id=str(product_id) if product_id is not None else None,
                    external_product_id=str(product_id) if product_id is not None else None,
                    seller_name=seller_name,
                    seller_external_id=seller_external_id,
                    seller_url=seller_url,
                    is_marketplace_offer=True,
                )
            )

        return products
