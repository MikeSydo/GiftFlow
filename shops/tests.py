from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from gifts.models import Category, Gift
from shops.discovery import QueryBuilder
from shops.connectors.factory import ConnectorFactory
from shops.connectors.generic import HtmlSearchTemplateConnector, JsonApiConnector, XmlFeedConnector
from shops.connectors.marketplace import MarketplaceTemplateConnector
from shops.models import ProductLink, Shop, ShopCategoryAlias, ShopIntegration, ShopSource
from shops.services import CategoryMatcher, MatchResult, _normalise
from shops.tasks import discover_source_products, process_discovered_product


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


class ConnectorFactoryTestCase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="Connector shop",
            slug="connector-shop",
            website="https://example.com",
            shop_type="specialized",
        )

    def _integration(self, connector_type, **kwargs):
        return ShopIntegration.objects.create(
            shop=self.shop,
            connector_type=connector_type,
            base_url="https://example.com",
            **kwargs,
        )

    def test_builds_json_api_connector(self):
        connector = ConnectorFactory.build(self._integration("json_api"))
        self.assertIsInstance(connector, JsonApiConnector)

    def test_builds_xml_feed_connector(self):
        connector = ConnectorFactory.build(self._integration("xml_feed"))
        self.assertIsInstance(connector, XmlFeedConnector)

    def test_builds_html_template_connector(self):
        connector = ConnectorFactory.build(self._integration("html_search_template"))
        self.assertIsInstance(connector, HtmlSearchTemplateConnector)

    def test_builds_marketplace_connector(self):
        connector = ConnectorFactory.build(
            self._integration("marketplace_template", request_config={"template": "rozetka"})
        )
        self.assertIsInstance(connector, MarketplaceTemplateConnector)


class ConnectorDiscoveryTestCase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="Discovery shop",
            slug="discovery-shop",
            website="https://example.com",
            shop_type="specialized",
        )

    def test_json_api_connector_parses_payload(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="json_api",
            base_url="https://api.example.com",
            field_mapping={
                "items_path": "items",
                "fields": {
                    "title": "title",
                    "url": "url",
                    "price": "price",
                    "seller": "seller.name",
                },
            },
        )
        source = ShopSource.objects.create(integration=integration, source_type="api_endpoint", value="https://api.example.com/items")
        connector = JsonApiConnector(integration)

        with patch.object(connector, "fetch_json", return_value={
            "items": [{"title": "Gamepad", "url": "https://example.com/p/1", "price": 1999, "seller": {"name": "Seller A"}}]
        }):
            products = connector.discover_products(source)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "Gamepad")
        self.assertEqual(products[0].seller_name, "Seller A")

    def test_json_api_connector_builds_query_url_from_seed_query(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="json_api",
            base_url="https://api.example.com/search",
            request_config={"search_url_template": "https://api.example.com/search?q={query}"},
            field_mapping={
                "items_path": "items",
                "fields": {"title": "title", "url": "url", "price": "price"},
            },
        )
        source = ShopSource.objects.create(
            integration=integration,
            source_type="seed_query",
            discovery_mode="query_seed",
            value="wireless gamepad",
        )
        connector = JsonApiConnector(integration)

        with patch.object(connector, "fetch_json", return_value={"items": []}) as mocked:
            connector.discover_products(source)

        mocked.assert_called_once_with("https://api.example.com/search?q=wireless+gamepad")

    def test_xml_feed_connector_parses_feed(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="xml_feed",
            base_url="https://feed.example.com",
            field_mapping={"items_path": ".//item", "fields": {"title": "title", "url": "link", "price": "price"}},
        )
        source = ShopSource.objects.create(integration=integration, source_type="feed_url", value="https://feed.example.com/feed.xml")
        connector = XmlFeedConnector(integration)

        xml = "<catalog><item><title>Mouse</title><link>https://example.com/mouse</link><price>999</price></item></catalog>"
        with patch.object(connector, "fetch_text", return_value=xml):
            products = connector.discover_products(source)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "Mouse")

    def test_html_connector_parses_listing(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="html_search_template",
            base_url="https://html.example.com",
            field_mapping={
                "item_selector": ".card",
                "fields": {
                    "title": ".title",
                    "url": {"selector": ".title", "attr": "href"},
                    "price": ".price",
                    "seller": ".seller",
                },
            },
        )
        source = ShopSource.objects.create(integration=integration, source_type="category_url", value="https://html.example.com/list")
        connector = HtmlSearchTemplateConnector(integration)
        html = """
        <div class="card">
          <a class="title" href="https://html.example.com/pad">Gamepad</a>
          <span class="price">2499</span>
          <span class="seller">Seller B</span>
        </div>
        """

        with patch.object(connector, "fetch_text", return_value=html):
            products = connector.discover_products(source)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].seller_name, "Seller B")

    def test_html_connector_uses_seed_query_template(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="html_search_template",
            base_url="https://html.example.com",
            request_config={"search_url_template": "https://html.example.com/search?q={query}"},
            field_mapping={"item_selector": ".missing", "fields": {}},
        )
        source = ShopSource.objects.create(
            integration=integration,
            source_type="seed_query",
            discovery_mode="query_seed",
            value="gift box",
        )
        connector = HtmlSearchTemplateConnector(integration)

        with patch.object(connector, "fetch_text", return_value="") as mocked:
            connector.discover_products(source)

        mocked.assert_called_once_with("https://html.example.com/search?q=gift+box")

    def test_marketplace_connector_uses_template_handler(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="marketplace_template",
            base_url="https://rozetka.com.ua",
            request_config={"template": "rozetka"},
        )
        source = ShopSource.objects.create(integration=integration, source_type="category_url", value="https://rozetka.com.ua/test")
        connector = MarketplaceTemplateConnector(integration)

        with patch.object(connector, "_discover_rozetka", return_value=[MagicMock(name="Offer")]) as mocked:
            products = connector.discover_products(source)

        mocked.assert_called_once_with(source.value)
        self.assertEqual(len(products), 1)

    def test_marketplace_connector_uses_query_seed_template(self):
        integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="marketplace_template",
            base_url="https://rozetka.com.ua",
            request_config={
                "template": "rozetka",
                "search_url_template": "https://rozetka.com.ua/ua/search/?text={query}",
            },
        )
        source = ShopSource.objects.create(
            integration=integration,
            source_type="seed_query",
            discovery_mode="query_seed",
            value="xbox controller",
        )
        connector = MarketplaceTemplateConnector(integration)

        with patch.object(connector, "_discover_rozetka", return_value=[MagicMock(name="Offer")]) as mocked:
            products = connector.discover_products(source)

        mocked.assert_called_once_with("https://rozetka.com.ua/ua/search/?text=xbox+controller")
        self.assertEqual(len(products), 1)


class QueryBuilderTestCase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="Query shop",
            slug="query-shop",
            website="https://example.com",
            shop_type="specialized",
        )
        self.gaming = Category.objects.create(name="Gaming", slug="gaming-query")
        self.shop.categories.add(self.gaming)
        Gift.objects.create(
            name="Xbox Wireless Controller",
            slug="xbox-wireless-controller",
            category=self.gaming,
            is_active=True,
            min_price=Decimal("1000.00"),
            max_price=Decimal("2000.00"),
        )
        Gift.objects.create(
            name="DualSense",
            slug="dualsense-query",
            category=self.gaming,
            is_active=True,
            min_price=Decimal("1500.00"),
            max_price=Decimal("2500.00"),
        )
        ShopCategoryAlias.objects.create(
            shop=self.shop,
            raw_category="Gamepads",
            normalized_category="gamepads",
            category=self.gaming,
            confidence=100,
            status=ShopCategoryAlias.STATUS_MATCHED,
        )
        self.integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="html_search_template",
            base_url="https://example.com",
            request_config={"seed_keywords": ["controller", "gamepad"], "max_queries": 10},
        )

    def test_query_seed_source_generates_queries_from_categories_aliases_and_gifts(self):
        source = ShopSource.objects.create(
            integration=self.integration,
            source_type="seed_query",
            discovery_mode="query_seed",
            value="wireless controller",
            config={"seed_keywords": ["joystick"]},
        )

        requests = QueryBuilder().build(source)
        queries = [request.value for request in requests]

        self.assertIn("wireless controller", queries)
        self.assertIn("controller", queries)
        self.assertIn("joystick", queries)
        self.assertIn("Gaming", queries)
        self.assertIn("Gamepads", queries)
        self.assertIn("Xbox Wireless Controller", queries)

    def test_category_seed_source_prefers_category_terms_without_manual_urls(self):
        source = ShopSource.objects.create(
            integration=self.integration,
            source_type="seed_query",
            discovery_mode="category_seed",
            value="",
        )

        requests = QueryBuilder().build(source)
        queries = [request.value for request in requests]

        self.assertIn("Gaming", queries)
        self.assertIn("Gamepads", queries)
        self.assertNotIn("DualSense", queries)

    def test_direct_feed_source_is_preserved(self):
        source = ShopSource.objects.create(
            integration=ShopIntegration.objects.create(
                shop=self.shop,
                connector_type="xml_feed",
                base_url="https://feed.example.com",
            ),
            source_type="feed_url",
            discovery_mode="feed",
            value="https://feed.example.com/feed.xml",
        )

        requests = QueryBuilder().build(source)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].value, "https://feed.example.com/feed.xml")

    def test_legacy_category_url_source_is_preserved_as_fallback(self):
        source = ShopSource.objects.create(
            integration=self.integration,
            source_type="category_url",
            discovery_mode="category_seed",
            value="https://example.com/category/gamepads",
        )

        requests = QueryBuilder().build(source)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].source_type, "category_url")
        self.assertEqual(requests[0].value, "https://example.com/category/gamepads")


class DiscoveryTaskQueryFlowTestCase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="Task shop",
            slug="task-shop",
            website="https://example.com",
            shop_type="specialized",
        )
        gaming = Category.objects.create(name="Gaming", slug="gaming-task")
        self.shop.categories.add(gaming)
        Gift.objects.create(
            name="Arcade Stick",
            slug="arcade-stick",
            category=gaming,
            is_active=True,
            min_price=Decimal("1000.00"),
            max_price=Decimal("2000.00"),
        )
        self.integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="html_search_template",
            base_url="https://example.com",
            request_config={
                "search_url_template": "https://example.com/search?q={query}",
                "seed_keywords": ["arcade stick"],
            },
            field_mapping={"item_selector": ".card", "fields": {"title": ".title", "url": ".title", "price": ".price"}},
        )
        self.source = ShopSource.objects.create(
            integration=self.integration,
            source_type="seed_query",
            discovery_mode="query_seed",
            value="fight stick",
        )

    @patch("shops.tasks.process_discovered_product.delay")
    @patch("shops.connectors.generic.HtmlSearchTemplateConnector.discover_products", return_value=[])
    def test_discovery_task_runs_multiple_generated_queries(self, mocked_discover, mocked_process):
        discover_source_products(self.source.id)

        self.assertGreaterEqual(mocked_discover.call_count, 2)
        self.assertEqual(mocked_process.call_count, 0)


class ProductOfferIngestionTestCase(TestCase):
    def setUp(self):
        CategoryMatcher.invalidate_cache()
        self.category = Category.objects.create(name="Gaming", slug="gaming")
        self.gift = Gift.objects.create(
            name="Gamepad",
            slug="gamepad",
            category=self.category,
            gender="U",
            age_min=0,
            age_max=100,
            min_price=Decimal("1000.00"),
            max_price=Decimal("3000.00"),
            is_active=True,
        )
        self.shop = Shop.objects.create(
            name="Rozetka",
            slug="rozetka",
            website="https://rozetka.com.ua",
            shop_type="marketplace",
        )
        self.integration = ShopIntegration.objects.create(
            shop=self.shop,
            connector_type="marketplace_template",
            base_url="https://rozetka.com.ua",
            request_config={"template": "rozetka"},
        )
        self.source = ShopSource.objects.create(
            integration=self.integration,
            source_type="category_url",
            value="https://rozetka.com.ua/ua/gamepads/",
            config={"category_hint": "Gaming"},
        )

    def tearDown(self):
        CategoryMatcher.invalidate_cache()

    def test_same_shop_can_store_multiple_offers_for_same_gift(self):
        process_discovered_product(
            {
                "name": "Gamepad",
                "url": "https://rozetka.com.ua/p1/",
                "price": "1999.00",
                "category_hint": "Gaming",
                "external_offer_id": "offer-1",
                "external_product_id": "prod-1",
                "seller_name": "Seller 1",
                "is_marketplace_offer": True,
            },
            self.source.id,
        )
        process_discovered_product(
            {
                "name": "Gamepad",
                "url": "https://rozetka.com.ua/p2/",
                "price": "1899.00",
                "category_hint": "Gaming",
                "external_offer_id": "offer-2",
                "external_product_id": "prod-1",
                "seller_name": "Seller 2",
                "is_marketplace_offer": True,
            },
            self.source.id,
        )

        offers = ProductLink.objects.filter(shop=self.shop, gift=self.gift).order_by("price")
        self.assertEqual(offers.count(), 2)
        self.assertEqual(offers.first().seller_name, "Seller 2")
        self.assertTrue(all(offer.is_marketplace_offer for offer in offers))
