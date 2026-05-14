import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from gifts.models import Category, Gift
from shops.models import ProductLink, Shop, ShopCategoryAlias, ShopClick
from shops.services import CategoryMatcher, MatchResult, _normalise


class NormaliseTestCase(TestCase):
    def test_lowercase(self):
        self.assertEqual(_normalise("Electronics"), "electronics")

    def test_strips_punctuation(self):
        self.assertEqual(_normalise("Home >> Kitchen!"), "home kitchen")

    def test_replaces_separators_with_space(self):
        result = _normalise("Sport/Outdoors")
        self.assertIn("sport", result)
        self.assertIn("outdoors", result)

    def test_unicode_accent_normalisation(self):
        result = _normalise("Électronique")
        self.assertIn("electronique", result)


class CategoryMatcherAliasTestCase(TestCase):
    def setUp(self):
        CategoryMatcher.invalidate_cache()
        self.shop = Shop.objects.create(
            name="Rozetka",
            slug="rozetka",
            website="https://example.com",
            shop_type="marketplace",
        )
        self.electronics = Category.objects.create(name="Electronics", slug="electronics")
        self.gaming = Category.objects.create(name="Gaming", slug="gaming")

    def tearDown(self):
        CategoryMatcher.invalidate_cache()

    def test_exact_alias_has_priority_over_fuzzy(self):
        ShopCategoryAlias.objects.create(
            shop=self.shop,
            raw_category="Console gear",
            normalized_category="console gear",
            category=self.gaming,
            confidence=100,
            status=ShopCategoryAlias.STATUS_MATCHED,
        )

        result = CategoryMatcher().match(shop=self.shop, raw="Console gear")

        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.category, self.gaming)
        self.assertFalse(result.needs_review)

    def test_unknown_category_goes_to_review_queue(self):
        result = CategoryMatcher(threshold=99.9).match(shop=self.shop, raw="Odd custom label")

        self.assertTrue(result.needs_review)
        alias = ShopCategoryAlias.objects.get(shop=self.shop, raw_category="Odd custom label")
        self.assertEqual(alias.status, ShopCategoryAlias.STATUS_PENDING)


class ProductLinkClickViewTestCase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Gaming", slug="gaming")
        self.gift = Gift.objects.create(
            name="Steam Deck Click",
            slug="steam-deck-click",
            category=self.category,
            gender="U",
            age_min=0,
            age_max=100,
        )
        self.shop = Shop.objects.create(
            name="Merchant",
            slug="merchant",
            website="https://merchant.example",
            shop_type="specialized",
            has_affiliate=True,
            affiliate_parameter="utm_source=giftflow",
        )
        self.link = ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop,
            product_url="https://merchant.example/product?sku=1",
            product_name="Steam Deck",
            price=Decimal("19499.00"),
            in_stock=True,
        )

    def test_click_endpoint_records_click_updates_counters_and_returns_affiliate_url(self):
        response = self.client.post(
            reverse("shops:productlink-click", args=[self.link.id]),
            data=json.dumps({"referrer": "https://giftflow.example/search/"}),
            content_type="application/json",
            HTTP_USER_AGENT="GiftFlow Test Browser",
            REMOTE_ADDR="203.0.113.10",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["affiliate_url"],
            "https://merchant.example/product?sku=1&utm_source=giftflow",
        )

        click = ShopClick.objects.get(product_link=self.link)
        self.assertEqual(click.referrer, "https://giftflow.example/search/")
        self.assertEqual(click.user_agent, "GiftFlow Test Browser")

        self.link.refresh_from_db()
        self.shop.refresh_from_db()
        self.assertEqual(self.link.click_count, 1)
        self.assertEqual(self.shop.click_count, 1)

