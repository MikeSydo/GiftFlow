from django.test import TestCase

from gifts.models import Category
from shops.models import Shop, ShopCategoryAlias
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

