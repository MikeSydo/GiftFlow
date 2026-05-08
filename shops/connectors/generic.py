from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

from .base import BaseConnector, ProductOfferData


class JsonApiConnector(BaseConnector):
    def discover_products(self, source) -> list[ProductOfferData]:
        payload = self.fetch_json(source.value)
        return self._parse_items(payload, source)

    def fetch_product_detail(self, product_link) -> ProductOfferData:
        detail_template = self.integration.request_config.get("detail_url_template")
        detail_url = detail_template.format(
            product_url=product_link.product_url,
            external_product_id=product_link.external_product_id or "",
            external_offer_id=product_link.external_offer_id or "",
        ) if detail_template else product_link.product_url
        payload = self.fetch_json(detail_url)
        items = self._parse_items(payload, None)
        if items:
            return items[0]
        return ProductOfferData(
            name=product_link.product_name,
            url=product_link.product_url,
            price=product_link.price,
            original_price=product_link.original_price,
            in_stock=product_link.in_stock,
            image_url=product_link.image_url,
            sku=product_link.sku,
            category_hint=product_link.original_category_name or None,
            external_offer_id=product_link.external_offer_id,
            external_product_id=product_link.external_product_id,
            seller_name=product_link.seller_name,
            seller_external_id=product_link.seller_external_id,
            seller_url=product_link.seller_url,
            is_marketplace_offer=product_link.is_marketplace_offer,
        )

    def _parse_items(self, payload: Any, source) -> list[ProductOfferData]:
        items_path = self.integration.field_mapping.get("items_path", "")
        fields = self.integration.field_mapping.get("fields", {})
        category_hint = None
        if source is not None:
            category_hint = source.config.get("category_hint") or source.value

        items = self.resolve_path(payload, items_path, payload)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []

        results: list[ProductOfferData] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = self.resolve_path(item, fields.get("title", "title"))
            url = self.resolve_path(item, fields.get("url", "url"))
            price = self.parse_decimal(self.resolve_path(item, fields.get("price", "price")))
            if not name or not url or price is None:
                continue

            original_price = self.parse_decimal(
                self.resolve_path(item, fields.get("original_price", "original_price"))
            )
            results.append(
                ProductOfferData(
                    name=str(name)[:300],
                    url=str(url),
                    price=price,
                    original_price=original_price,
                    in_stock=self.as_bool(self.resolve_path(item, fields.get("availability", "in_stock"), True)),
                    image_url=self.resolve_path(item, fields.get("image", "image_url")),
                    sku=self.resolve_path(item, fields.get("sku", "sku")),
                    description=self.resolve_path(item, fields.get("description", "description")),
                    category_hint=self.resolve_path(item, fields.get("category", "category")) or category_hint,
                    external_offer_id=self.resolve_path(item, fields.get("external_offer_id", "external_offer_id")),
                    external_product_id=self.resolve_path(item, fields.get("external_product_id", "external_product_id")),
                    seller_name=self.resolve_path(item, fields.get("seller", "seller_name")),
                    seller_external_id=self.resolve_path(item, fields.get("seller_external_id", "seller_external_id")),
                    seller_url=self.resolve_path(item, fields.get("seller_url", "seller_url")),
                    is_marketplace_offer=self.as_bool(
                        self.resolve_path(item, fields.get("is_marketplace_offer", "is_marketplace_offer"))
                    ),
                )
            )

        return results


class XmlFeedConnector(BaseConnector):
    def discover_products(self, source) -> list[ProductOfferData]:
        root = ElementTree.fromstring(self.fetch_text(source.value))
        item_xpath = self.integration.field_mapping.get("items_path", ".//item")
        fields = self.integration.field_mapping.get("fields", {})
        category_hint = source.config.get("category_hint") or source.value
        results: list[ProductOfferData] = []

        for item in root.findall(item_xpath):
            name = item.findtext(fields.get("title", "title"))
            url = item.findtext(fields.get("url", "link"))
            price = self.parse_decimal(item.findtext(fields.get("price", "price")))
            if not name or not url or price is None:
                continue

            original_price = self.parse_decimal(item.findtext(fields.get("original_price", "oldprice")))
            results.append(
                ProductOfferData(
                    name=name[:300],
                    url=url,
                    price=price,
                    original_price=original_price,
                    in_stock=self.as_bool(item.findtext(fields.get("availability", "availability"), "true")),
                    image_url=item.findtext(fields.get("image", "image")),
                    sku=item.findtext(fields.get("sku", "sku")),
                    description=item.findtext(fields.get("description", "description")),
                    category_hint=item.findtext(fields.get("category", "category")) or category_hint,
                    external_offer_id=item.findtext(fields.get("external_offer_id", "id")),
                    external_product_id=item.findtext(fields.get("external_product_id", "product_id")),
                    seller_name=item.findtext(fields.get("seller", "seller")),
                    seller_external_id=item.findtext(fields.get("seller_external_id", "seller_id")),
                    seller_url=item.findtext(fields.get("seller_url", "seller_url")),
                    is_marketplace_offer=self.as_bool(item.findtext(fields.get("is_marketplace_offer", "is_marketplace_offer"))),
                )
            )

        return results

    def fetch_product_detail(self, product_link) -> ProductOfferData:
        return ProductOfferData(
            name=product_link.product_name,
            url=product_link.product_url,
            price=product_link.price,
            original_price=product_link.original_price,
            in_stock=product_link.in_stock,
            image_url=product_link.image_url,
            sku=product_link.sku,
            category_hint=product_link.original_category_name or None,
            external_offer_id=product_link.external_offer_id,
            external_product_id=product_link.external_product_id,
            seller_name=product_link.seller_name,
            seller_external_id=product_link.seller_external_id,
            seller_url=product_link.seller_url,
            is_marketplace_offer=product_link.is_marketplace_offer,
        )


class HtmlSearchTemplateConnector(BaseConnector):
    def discover_products(self, source) -> list[ProductOfferData]:
        url = self._build_source_url(source)
        html = self.fetch_text(url)
        item_selector = self.integration.field_mapping.get("item_selector")
        field_mapping = self.integration.field_mapping.get("fields", {})
        category_hint = source.config.get("category_hint") or source.value
        if not item_selector:
            return []
        return self.parse_html_listing(html, item_selector, field_mapping, category_hint=category_hint)

    def fetch_product_detail(self, product_link) -> ProductOfferData:
        html = self.fetch_text(product_link.product_url)
        item_selector = self.integration.field_mapping.get("detail_selector")
        fields = self.integration.field_mapping.get("detail_fields") or self.integration.field_mapping.get("fields", {})
        if item_selector:
            products = self.parse_html_listing(
                html,
                item_selector,
                fields,
                category_hint=product_link.original_category_name or None,
                default_marketplace=product_link.is_marketplace_offer,
            )
            if products:
                return products[0]
        return ProductOfferData(
            name=product_link.product_name,
            url=product_link.product_url,
            price=product_link.price,
            original_price=product_link.original_price,
            in_stock=product_link.in_stock,
            image_url=product_link.image_url,
            sku=product_link.sku,
            category_hint=product_link.original_category_name or None,
            seller_name=product_link.seller_name,
            seller_external_id=product_link.seller_external_id,
            seller_url=product_link.seller_url,
            external_offer_id=product_link.external_offer_id,
            external_product_id=product_link.external_product_id,
            is_marketplace_offer=product_link.is_marketplace_offer,
        )

    def _build_source_url(self, source) -> str:
        if source.source_type == "seed_query":
            template = (
                source.config.get("search_url_template")
                or self.integration.request_config.get("search_url_template")
            )
            if not template:
                return source.value
            return template.format(query=quote_plus(source.value))
        return source.value
