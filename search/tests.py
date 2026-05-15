from django.contrib import admin
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, Client, RequestFactory
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx

from gifts.models import Gift, Category, Tag
from shops.models import PriceHistory, ProductLink, Shop

from .hotline import HotlineAdapter, HotlineMerchantOffer, HotlineOffer
from .admin import IngestionRunAdmin
from .models import IngestionRun
from .seeds import HotlineSeed
from .services import upsert_hotline_gift_from_summary
from .tasks import refresh_hotline_product

FIXTURE_DIR = Path(__file__).resolve().parent / "test_fixtures"


class SearchGiftsAPITestCase(TestCase):
    """Tests for search_gifts_api endpoint"""

    def setUp(self):
        """Set up test data"""
        self.client = Client()
        self.cache_delay_patcher = patch("shops.tasks.update_gift_price_cache.delay")
        self.cache_delay_patcher.start()
        self.addCleanup(self.cache_delay_patcher.stop)

        # Create categories
        self.category1 = Category.objects.create(
            name="Electronics",
            slug="electronics",
            is_active=True
        )
        self.category2 = Category.objects.create(
            name="Books",
            slug="books",
            is_active=True
        )

        # Create tags
        self.tag_birthday = Tag.objects.create(
            name="Birthday",
            slug="birthday",
            tag_type="O"  # occasion
        )
        self.tag_tech = Tag.objects.create(
            name="Tech lover",
            slug="tech-lover",
            tag_type="I"  # interest
        )
        self.tag_friend = Tag.objects.create(
            name="Friend",
            slug="friend",
            tag_type="R"  # relationship
        )

        # Create gifts
        self.gift1 = Gift.objects.create(
            name="Smartphone",
            slug="smartphone",
            description="Latest smartphone",
            short_description="Cool phone",
            gender="M",
            age_min=18,
            age_max=50,
            min_price=Decimal("500.00"),
            max_price=Decimal("1000.00"),
            category=self.category1,
            popularity_score=100,
            is_active=True,
            is_featured=True
        )
        self.gift1.tags.add(self.tag_birthday, self.tag_tech)

        self.gift2 = Gift.objects.create(
            name="Headphones",
            slug="headphones",
            description="Wireless headphones",
            short_description="Great sound",
            gender="U",
            age_min=15,
            age_max=60,
            min_price=Decimal("100.00"),
            max_price=Decimal("300.00"),
            category=self.category1,
            popularity_score=80,
            is_active=True,
            is_featured=False
        )
        self.gift2.tags.add(self.tag_tech)

        self.gift3 = Gift.objects.create(
            name="Novel Book",
            slug="novel-book",
            description="Bestselling novel",
            short_description="Great read",
            gender="F",
            age_min=20,
            age_max=70,
            min_price=Decimal("10.00"),
            max_price=Decimal("30.00"),
            category=self.category2,
            popularity_score=60,
            is_active=True,
            is_featured=False
        )
        self.gift3.tags.add(self.tag_birthday, self.tag_friend)

        self.shop1 = Shop.objects.create(
            name="Rozetka",
            slug="rozetka",
            website="https://rozetka.com.ua",
            shop_type="marketplace",
        )
        self.shop2 = Shop.objects.create(
            name="Yabluka",
            slug="yabluka",
            website="https://yabluka.ua",
            shop_type="brand",
        )

        ProductLink.objects.create(
            gift=self.gift1,
            shop=self.shop1,
            product_url="https://rozetka.com.ua/pad-1/",
            product_name="Smartphone offer A",
            price=Decimal("550.00"),
            original_price=Decimal("600.00"),
            in_stock=True,
            seller_name="Seller A",
            external_offer_id="offer-a",
        )
        ProductLink.objects.create(
            gift=self.gift1,
            shop=self.shop2,
            product_url="https://yabluka.ua/pad-1/",
            product_name="Smartphone offer B",
            price=Decimal("530.00"),
            in_stock=True,
            seller_name="Yabluka",
            external_offer_id="offer-b",
        )
        ProductLink.objects.create(
            gift=self.gift2,
            shop=self.shop1,
            product_url="https://rozetka.com.ua/headphones-1/",
            product_name="Headphones offer",
            price=Decimal("120.00"),
            in_stock=True,
            seller_name="Seller C",
            external_offer_id="offer-c",
        )

        # Inactive gift (should not appear in results)
        self.gift4 = Gift.objects.create(
            name="Inactive Gift",
            slug="inactive-gift",
            gender="U",
            age_min=0,
            age_max=100,
            min_price=Decimal("50.00"),
            max_price=Decimal("100.00"),
            category=self.category1,
            is_active=False
        )

    def test_search_api_without_filters(self):
        """Test API returns all active gifts without filters"""
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 3)  # Only active gifts

        # Check gifts are ordered by popularity
        self.assertEqual(data['results'][0]['title'], 'Smartphone')
        self.assertEqual(data['results'][1]['title'], 'Headphones')
        self.assertEqual(data['results'][2]['title'], 'Novel Book')

    def test_search_api_filter_by_category(self):
        """Test filtering by category"""
        response = self.client.get('/search/api/', {
            'category': self.category1.id
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['count'], 2)
        categories = [gift['category'] for gift in data['results']]
        self.assertTrue(all(cat == 'Electronics' for cat in categories))

    def test_search_api_filter_by_gender_male(self):
        """Test filtering by male gender includes male and unisex gifts"""
        response = self.client.get('/search/api/', {
            'gender': 'M'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return Smartphone (M) and Headphones (U)
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Smartphone', titles)
        self.assertIn('Headphones', titles)
        self.assertNotIn('Novel Book', titles)

    def test_search_api_filter_by_gender_female(self):
        """Test filtering by female gender includes female and unisex gifts"""
        response = self.client.get('/search/api/', {
            'gender': 'F'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return Novel Book (F) and Headphones (U)
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Novel Book', titles)
        self.assertIn('Headphones', titles)
        self.assertNotIn('Smartphone', titles)

    def test_search_api_filter_by_age(self):
        """Test filtering by age range"""
        response = self.client.get('/search/api/', {
            'age': '25'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # All three gifts match age 25
        self.assertEqual(data['count'], 3)

    def test_search_api_filter_by_age_young(self):
        """Test filtering by young age"""
        response = self.client.get('/search/api/', {
            'age': '16'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Only Headphones (15-60)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Headphones')

    def test_search_api_filter_by_budget_min(self):
        """Test filtering by minimum budget"""
        response = self.client.get('/search/api/', {
            'budget_min': '150'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return only gifts with min_price >= 150
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Smartphone')

    def test_search_api_filter_by_budget_max(self):
        """Test filtering by maximum budget"""
        response = self.client.get('/search/api/', {
            'budget_max': '200'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return gifts with min_price <= 200
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Headphones', titles)
        self.assertIn('Novel Book', titles)

    def test_search_api_filter_by_budget_range(self):
        """Test filtering by budget range"""
        response = self.client.get('/search/api/', {
            'budget_min': '50',
            'budget_max': '150'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Headphones')

    def test_search_api_filter_by_tags(self):
        """Test filtering by tags"""
        response = self.client.get('/search/api/', {
            'tags': f'{self.tag_birthday.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Smartphone and Novel Book have birthday tag
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Smartphone', titles)
        self.assertIn('Novel Book', titles)

    def test_search_api_filter_by_multiple_tags(self):
        """Test filtering by multiple tags"""
        response = self.client.get('/search/api/', {
            'tags': f'{self.tag_birthday.id},{self.tag_tech.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return gifts that have any of these tags
        self.assertEqual(data['count'], 3)

    def test_search_api_combined_filters(self):
        """Test combining multiple filters"""
        response = self.client.get('/search/api/', {
            'category': self.category1.id,
            'gender': 'M',
            'age': '30',
            'budget_min': '400',
            'budget_max': '600',
            'tags': f'{self.tag_tech.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return only Smartphone
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Smartphone')

    def test_search_api_response_structure(self):
        """Test response has correct structure"""
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('results', data)
        self.assertIn('count', data)

        if data['results']:
            gift = data['results'][0]
            self.assertIn('id', gift)
            self.assertIn('title', gift)
            self.assertIn('short_description', gift)
            self.assertIn('image', gift)
            self.assertIn('category', gift)
            self.assertIn('min_price', gift)
            self.assertIn('popularity_score', gift)
            self.assertIn('tags', gift)
            self.assertIn('best_offer', gift)
            self.assertIsInstance(gift['tags'], list)

    def test_search_api_returns_best_offer(self):
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        smartphone = next(item for item in data["results"] if item["title"] == "Smartphone")

        self.assertIsNotNone(smartphone["best_offer"])
        self.assertEqual(smartphone["best_offer"]["shop"], "Yabluka")
        self.assertEqual(smartphone["best_offer"]["price"], "530.00")

    def test_search_api_method_not_allowed(self):
        """Test POST method returns error"""
        response = self.client.post('/search/api/', {})

        self.assertEqual(response.status_code, 405)
        data = response.json()
        self.assertIn('error', data)

    def test_search_api_invalid_parameters(self):
        """Test API handles invalid parameters gracefully"""
        response = self.client.get('/search/api/', {
            'category': 'invalid',
            'age': 'not_a_number',
            'budget_min': 'abc',
            'tags': 'xyz'
        })

        # Should not crash, just return all gifts
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)

    def test_search_api_max_results_limit(self):
        """Test API limits results to 20 items"""
        # Create 25 gifts
        for i in range(25):
            Gift.objects.create(
                name=f"Gift {i}",
                slug=f"gift-{i}",
                gender="U",
                age_min=0,
                age_max=100,
                min_price=Decimal("10.00"),
                max_price=Decimal("50.00"),
                category=self.category1,
                popularity_score=i,
                is_active=True
            )

        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return max 20 results
        self.assertEqual(data['count'], 20)

    def test_search_api_excludes_inactive_gifts(self):
        """Test API does not return inactive gifts"""
        response = self.client.get('/search/api/')

        data = response.json()
        titles = [gift['title'] for gift in data['results']]

        self.assertNotIn('Inactive Gift', titles)

    def test_search_api_query_filters_local_results(self):
        response = self.client.get('/search/api/', {'q': 'head'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Headphones')

    def test_search_api_query_searches_tags_and_categories(self):
        response = self.client.get('/search/api/', {'q': 'electronics'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Smartphone', titles)
        self.assertIn('Headphones', titles)

    def test_search_api_returns_detail_url_for_gifts(self):
        response = self.client.get('/search/api/', {'q': 'smart'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(
            data['results'][0]['detail_url'],
            reverse('gifts:gift_detail', args=[self.gift1.slug]),
        )


class IngestionRunAdminActionTestCase(TestCase):
    def setUp(self):
        self.request = RequestFactory().post("/admin/search/ingestionrun/")
        self.model_admin = IngestionRunAdmin(IngestionRun, admin.site)
        self.model_admin.message_user = Mock()
        self.gift = Gift.objects.create(
            name="Steam Deck Admin",
            slug="steam-deck-admin",
            gender="U",
            age_min=0,
            age_max=100,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="21916104",
            source_product_url="https://hotline.ua/ua/computer-igrovye-pristavki/steam-deck-256-gb/",
        )

    @patch("search.admin.queue_hotline_seed_refresh")
    def test_retry_failed_runs_queues_only_failed_runs(self, mocked_queue):
        failed = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key="gaming-gamepads",
            status=IngestionRun.STATUS_FAILED,
        )
        completed = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key="gaming-laptops",
            status=IngestionRun.STATUS_COMPLETED,
        )

        self.model_admin.retry_failed_runs(
            self.request,
            IngestionRun.objects.filter(id__in=[failed.id, completed.id]),
        )

        mocked_queue.assert_called_once_with("gaming-gamepads")
        self.model_admin.message_user.assert_called_once()

    @patch("search.admin.queue_hotline_product_refresh")
    def test_requeue_selected_runs_queues_product_refresh_for_gift(self, mocked_queue):
        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_PRODUCT_REFRESH,
            gift=self.gift,
            source_product_id=self.gift.source_product_id,
            status=IngestionRun.STATUS_COMPLETED,
        )

        self.model_admin.requeue_selected_runs(
            self.request,
            IngestionRun.objects.filter(id=run.id),
        )

        mocked_queue.assert_called_once_with(self.gift)
        self.model_admin.message_user.assert_called_once()

    @patch("search.admin.queue_hotline_seed_refresh")
    def test_queue_all_hotline_seed_refreshes_queues_each_seed(self, mocked_queue):
        self.model_admin.queue_all_hotline_seed_refreshes(
            self.request,
            IngestionRun.objects.none(),
        )

        self.assertGreaterEqual(mocked_queue.call_count, 1)
        self.model_admin.message_user.assert_called_once()


class HotlineProductOfferParserTestCase(TestCase):
    def test_parse_search_html_fixture_extracts_hotline_product_summaries(self):
        html = (FIXTURE_DIR / "hotline_search_nuxt.html").read_text(encoding="utf-8")

        offers = HotlineAdapter()._parse_search_html(html)

        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].external_product_id, "21916104")
        self.assertEqual(offers[0].external_offer_id, "hotline-product-21916104")
        self.assertEqual(offers[0].title, "Steam Deck 256 GB")
        self.assertEqual(
            offers[0].product_url,
            "https://hotline.ua/ua/computer-igrovye-pristavki/steam-deck-256-gb/",
        )
        self.assertEqual(offers[0].image_url, "https://hotline.ua/img/steam-deck.jpg")
        self.assertEqual(offers[0].price, Decimal("19499"))
        self.assertEqual(offers[0].original_price, Decimal("21395"))

    def test_parse_product_html_fixture_extracts_merchant_offers(self):
        html = (FIXTURE_DIR / "hotline_product_nuxt.html").read_text(encoding="utf-8")

        offers = HotlineAdapter()._parse_product_html(html, external_product_id="21916104")

        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].external_offer_id, "101")
        self.assertEqual(offers[0].seller_name, "GRO")
        self.assertEqual(offers[0].seller_external_id, "77")
        self.assertEqual(offers[0].seller_url, "https://gro.ua")
        self.assertEqual(offers[0].original_price, Decimal("21395"))
        self.assertEqual(offers[1].product_url, "https://hotline.ua/go/price/102/")

    def test_parse_product_html_extracts_merchant_offers_and_old_price(self):
        html = """
        <script>
        window.__NUXT__={offers:{edges:[
            {node:{_id:"101",conversionUrl:"\\u002Fgo\\u002Fprice\\u002F101\\u002F",descriptionShort:"Steam Deck 256 GB",firmId:77,firmTitle:"GRO",firmExtraInfo:{website:"gro.ua"},price:19499,visible:true}},
            {node:{_id:"102",conversionUrl:"\\u002Fgo\\u002Fprice\\u002F102\\u002F",descriptionFull:"Steam Deck 256 GB",firmId:78,firmTitle:"UPPS.UA",firmExtraInfo:{website:"upps.ua"},price:20599,visible:true}}
        ],pageInfo:{}},sales:{sales:{"101":{oldPrice:21395}}}};
        </script>
        """

        adapter = HotlineAdapter()
        offers = adapter._parse_product_html(html, external_product_id="21916104")

        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].external_offer_id, "101")
        self.assertEqual(offers[0].seller_name, "GRO")
        self.assertEqual(offers[0].seller_url, "https://gro.ua")
        self.assertEqual(offers[0].original_price, Decimal("21395"))
        self.assertEqual(offers[1].product_url, "https://hotline.ua/go/price/102/")


class HotlineGiftImageCacheTestCase(TestCase):
    @staticmethod
    def _storage_settings(destination_root):
        return {
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {
                    "location": destination_root,
                    "base_url": "/uploaded-media/",
                },
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }

    @staticmethod
    def _seed():
        return HotlineSeed(
            key="gaming-gamepads",
            query="gamepad",
            category_slug="gaming",
            category_name="Gaming",
        )

    @staticmethod
    def _summary(image_url="https://hotline.ua/img/gamepad.jpg"):
        return HotlineOffer(
            external_product_id="659422",
            external_offer_id="hotline-product-659422",
            title="Gamepad Xbox Wireless",
            product_url="https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/659422/",
            image_url=image_url,
            price=Decimal("2199"),
            original_price=Decimal("2599"),
        )

    @patch("search.services.httpx.Client")
    def test_upsert_hotline_gift_caches_image_in_default_storage(self, mocked_client):
        response = Mock()
        response.headers = {"content-type": "image/gif"}
        response.content = b"gif-bytes"
        response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.return_value = response

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                gift, created, changed = upsert_hotline_gift_from_summary(
                    self._seed(),
                    self._summary(),
                )

                self.assertTrue(created)
                self.assertTrue(changed)
                self.assertEqual(gift.image_url, "https://hotline.ua/img/gamepad.jpg")
                self.assertEqual(gift.image.name, "gifts/hotline/659422.gif")
                self.assertTrue(default_storage.exists(gift.image.name))
                with default_storage.open(gift.image.name, "rb") as cached_file:
                    self.assertEqual(cached_file.read(), b"gif-bytes")

        mocked_client.return_value.__enter__.return_value.get.assert_called_once_with(
            "https://hotline.ua/img/gamepad.jpg",
        )

    @patch("search.services.httpx.Client")
    def test_upsert_hotline_gift_keeps_image_url_when_cache_download_fails(self, mocked_client):
        mocked_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError(
            "network failed",
        )

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                gift, created, changed = upsert_hotline_gift_from_summary(
                    self._seed(),
                    self._summary(),
                )

        self.assertTrue(created)
        self.assertTrue(changed)
        self.assertEqual(gift.image_url, "https://hotline.ua/img/gamepad.jpg")
        self.assertFalse(gift.image)

    @patch("search.services.httpx.Client")
    def test_cache_hotline_images_command_caches_existing_image_urls(self, mocked_client):
        response = Mock()
        response.headers = {"content-type": "image/jpeg"}
        response.content = b"image-bytes"
        response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.return_value = response

        gift = Gift.objects.create(
            name="Existing Hotline Gift",
            slug="existing-hotline-gift",
            gender="U",
            age_min=0,
            age_max=100,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="12345",
            image_url="https://hotline.ua/img/existing.jpg",
        )

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                output = StringIO()
                call_command("cache_hotline_images", stdout=output)

                gift.refresh_from_db()
                self.assertEqual(gift.image.name, "gifts/hotline/12345.jpg")
                self.assertTrue(default_storage.exists(gift.image.name))
                self.assertIn("cached=1", output.getvalue())


class HotlineProductRefreshTaskTestCase(TestCase):
    def setUp(self):
        self.cache_delay_patcher = patch("shops.tasks.update_gift_price_cache.delay")
        self.cache_delay_mock = self.cache_delay_patcher.start()
        self.addCleanup(self.cache_delay_patcher.stop)

        self.category = Category.objects.create(
            name="Gaming",
            slug="gaming",
            is_active=True,
        )
        self.gift = Gift.objects.create(
            name="Steam Deck 256 GB",
            slug="steam-deck-256-gb",
            category=self.category,
            gender="U",
            age_min=0,
            age_max=100,
            min_price=Decimal("20000.00"),
            max_price=Decimal("22000.00"),
            popularity_score=10,
            is_active=True,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="21916104",
            source_product_url="https://hotline.ua/ua/computer-igrovye-pristavki/steam-deck-256-gb/",
        )
        self.stale_shop = Shop.objects.create(
            name="Old Shop",
            slug="old-shop",
            website="https://old-shop.example",
            shop_type="specialized",
        )
        self.stale_link = ProductLink.objects.create(
            gift=self.gift,
            shop=self.stale_shop,
            product_url="https://hotline.ua/go/price/999/",
            product_name="Steam Deck 256 GB",
            price=Decimal("21500.00"),
            original_price=Decimal("22000.00"),
            in_stock=True,
            last_checked=timezone.now(),
            last_price_update=timezone.now(),
            external_offer_id="999",
            external_product_id="21916104",
            seller_name="Old Shop",
            seller_url="https://old-shop.example",
            is_marketplace_offer=True,
        )
        PriceHistory.objects.create(
            product_link=self.stale_link,
            price=Decimal("21500.00"),
            in_stock=True,
        )

    @staticmethod
    def _merchant_offer(
        offer_id: str,
        seller_name: str,
        website: str,
        price: str,
        old_price: str | None = None,
    ) -> HotlineMerchantOffer:
        return HotlineMerchantOffer(
            external_product_id="21916104",
            external_offer_id=offer_id,
            title="Steam Deck 256 GB",
            product_url=f"https://hotline.ua/go/price/{offer_id}/",
            price=Decimal(price),
            original_price=Decimal(old_price) if old_price else None,
            seller_name=seller_name,
            seller_external_id=f"seller-{offer_id}",
            seller_url=f"https://{website}",
        )

    @patch("search.tasks.HotlineAdapter.fetch_product_offers")
    def test_refresh_hotline_product_creates_merchants_and_marks_missing_offers_stale(self, mocked_fetch):
        mocked_fetch.return_value = [
            self._merchant_offer("101", "GRO", "gro.ua", "19499", "21395"),
            self._merchant_offer("102", "UPPS.UA", "upps.ua", "20599"),
        ]

        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_PRODUCT_REFRESH,
            gift=self.gift,
            source_product_id=self.gift.source_product_id,
        )

        self.cache_delay_mock.reset_mock()
        refresh_hotline_product(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, IngestionRun.STATUS_COMPLETED)
        self.assertEqual(run.discovered_count, 2)

        offers = ProductLink.objects.filter(gift=self.gift).order_by("external_offer_id")
        self.assertEqual(offers.count(), 3)

        gro_offer = offers.get(external_offer_id="101")
        self.assertEqual(gro_offer.shop.name, "GRO")
        self.assertEqual(gro_offer.price, Decimal("19499"))
        self.assertEqual(gro_offer.original_price, Decimal("21395"))
        self.assertTrue(gro_offer.in_stock)
        self.assertTrue(gro_offer.is_marketplace_offer)

        self.stale_link.refresh_from_db()
        self.assertFalse(self.stale_link.in_stock)
        self.assertEqual(
            PriceHistory.objects.filter(product_link=self.stale_link).order_by("-recorded_at").first().in_stock,
            False,
        )

        self.gift.refresh_from_db()
        self.assertEqual(self.gift.min_price, Decimal("19499.00"))
        self.assertEqual(self.gift.max_price, Decimal("20599.00"))
        self.cache_delay_mock.assert_not_called()

    def test_product_link_save_still_queues_price_cache_update(self):
        self.cache_delay_mock.reset_mock()

        ProductLink.objects.create(
            gift=self.gift,
            shop=self.stale_shop,
            product_url="https://example.com/standalone-offer",
            product_name="Standalone Offer",
            price=Decimal("19999.00"),
            in_stock=True,
        )

        self.cache_delay_mock.assert_called_once_with(self.gift.id)
