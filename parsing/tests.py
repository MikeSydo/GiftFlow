from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, RequestFactory
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
from .admin import HotlineSeedAdmin, IngestionRunAdmin
from .models import HotlineSeed as DbHotlineSeed, IngestionRun
from .seed_catalog import import_hotline_seed_templates
from .seeds import HOTLINE_SEEDS, HotlineSeed
from .services import (
    cache_shop_logo,
    extract_hotline_og_image_url,
    resolve_hotline_product_image_url,
    upsert_hotline_gift_from_summary,
)
from .tasks import queue_missing_hotline_seed_refreshes, refresh_hotline_product

FIXTURE_DIR = Path(__file__).resolve().parent / "test_fixtures"



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
        self.seed = DbHotlineSeed.objects.create(
            key="gaming-gamepads",
            query="gamepad",
            category=Category.objects.get_or_create(
                name="Gamepads",
                slug="gaming-gamepads",
                parent=Category.objects.get_or_create(name="Gaming", slug="gaming")[0],
            )[0],
            is_active=True,
        )

    @patch("parsing.admin.queue_hotline_seed_refresh")
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

    @patch("parsing.admin.queue_hotline_product_refresh")
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

    @patch("parsing.admin.queue_hotline_seed_refresh")
    def test_queue_all_hotline_seed_refreshes_queues_each_seed(self, mocked_queue):
        self.model_admin.queue_all_hotline_seed_refreshes(
            self.request,
            IngestionRun.objects.none(),
        )

        self.assertGreaterEqual(mocked_queue.call_count, 1)
        self.model_admin.message_user.assert_called_once()


class ColdStartHotlineBootstrapTestCase(TestCase):
    def setUp(self):
        import_hotline_seed_templates()

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_queues_all_missing_seed_keys_on_empty_database(self, mocked_delay):
        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, len(HOTLINE_SEEDS))
        self.assertEqual(
            IngestionRun.objects.filter(
                task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
                status=IngestionRun.STATUS_PENDING,
            ).count(),
            len(HOTLINE_SEEDS),
        )
        self.assertEqual(mocked_delay.call_count, len(HOTLINE_SEEDS))

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_skips_seed_keys_that_already_completed(self, mocked_delay):
        completed_seed = HOTLINE_SEEDS[0]
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=completed_seed.key,
            status=IngestionRun.STATUS_COMPLETED,
        )

        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, len(HOTLINE_SEEDS) - 1)
        self.assertEqual(mocked_delay.call_count, len(HOTLINE_SEEDS) - 1)
        self.assertEqual(
            IngestionRun.objects.filter(seed_key=completed_seed.key).count(),
            1,
        )

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_requeues_failed_seed_keys(self, mocked_delay):
        failed_seed = HOTLINE_SEEDS[0]
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=failed_seed.key,
            status=IngestionRun.STATUS_FAILED,
        )

        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, len(HOTLINE_SEEDS))
        self.assertEqual(mocked_delay.call_count, len(HOTLINE_SEEDS))
        self.assertEqual(
            IngestionRun.objects.filter(seed_key=failed_seed.key).count(),
            2,
        )

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_does_not_duplicate_active_seed_runs(self, mocked_delay):
        active_seed = HOTLINE_SEEDS[0]
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=active_seed.key,
            status=IngestionRun.STATUS_PENDING,
        )

        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, len(HOTLINE_SEEDS) - 1)
        self.assertEqual(mocked_delay.call_count, len(HOTLINE_SEEDS) - 1)
        self.assertEqual(
            IngestionRun.objects.filter(seed_key=active_seed.key).count(),
            1,
        )

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_ignores_inactive_database_seeds(self, mocked_delay):
        inactive_seed = HOTLINE_SEEDS[0]
        DbHotlineSeed.objects.filter(key=inactive_seed.key).update(is_active=False)

        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, len(HOTLINE_SEEDS) - 1)
        self.assertEqual(mocked_delay.call_count, len(HOTLINE_SEEDS) - 1)
        self.assertFalse(IngestionRun.objects.filter(seed_key=inactive_seed.key).exists())

    def test_seed_template_import_is_idempotent(self):
        first_created, first_updated = import_hotline_seed_templates()
        second_created, second_updated = import_hotline_seed_templates()

        self.assertEqual(first_created, 0)
        self.assertEqual(first_updated, 0)
        self.assertEqual(second_created, 0)
        self.assertEqual(second_updated, 0)
        self.assertEqual(DbHotlineSeed.objects.count(), len(HOTLINE_SEEDS))

    def test_seed_template_import_preserves_inactive_admin_choice(self):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.is_active = False
        seed.save(update_fields=["is_active"])

        import_hotline_seed_templates()

        seed.refresh_from_db()
        self.assertFalse(seed.is_active)

    def test_import_hotline_categories_command_is_idempotent(self):
        output = StringIO()

        call_command("import_hotline_categories", stdout=output)
        first_count = DbHotlineSeed.objects.count()
        call_command("import_hotline_categories", stdout=output)
        second_count = DbHotlineSeed.objects.count()

        self.assertEqual(first_count, len(HOTLINE_SEEDS))
        self.assertEqual(second_count, len(HOTLINE_SEEDS))
        self.assertTrue(Category.objects.filter(parent__isnull=True).exists())
        self.assertTrue(Category.objects.filter(parent__isnull=False).exists())
        self.assertTrue(Category.objects.filter(name="Смартфони, Смарт-годинники").exists())
        self.assertTrue(Category.objects.filter(name="Смартфони та мобільні телефони").exists())

    def test_seed_hotline_seeds_command_is_removed(self):
        with self.assertRaises(CommandError):
            call_command("seed_hotline_seeds")


class HotlineSeedAdminActionTestCase(TestCase):
    def setUp(self):
        self.request = RequestFactory().post("/admin/search/hotlineseed/")
        self.model_admin = HotlineSeedAdmin(DbHotlineSeed, admin.site)
        self.model_admin.message_user = Mock()
        self.parent_category = Category.objects.create(name="Gaming", slug="gaming")
        self.category = Category.objects.create(
            name="Gamepads",
            slug="gaming-gamepads",
            parent=self.parent_category,
        )
        self.seed = DbHotlineSeed.objects.create(
            key="gaming-gamepads",
            query="gamepad",
            source_url="https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/",
            category=self.category,
            is_active=True,
        )

    @patch("parsing.admin.queue_hotline_seed_refresh")
    def test_queue_selected_seeds_queues_active_seed(self, mocked_queue):
        self.model_admin.queue_selected_seeds(
            self.request,
            DbHotlineSeed.objects.filter(id=self.seed.id),
        )

        mocked_queue.assert_called_once()
        self.assertEqual(mocked_queue.call_args.args[0].key, self.seed.key)
        self.model_admin.message_user.assert_called_once()

    def test_clone_selected_seeds_creates_inactive_copy(self):
        self.model_admin.clone_selected_seeds(
            self.request,
            DbHotlineSeed.objects.filter(id=self.seed.id),
        )

        clone = DbHotlineSeed.objects.get(key="gaming-gamepads-copy")
        self.assertFalse(clone.is_active)
        self.assertEqual(clone.query, self.seed.query)
        self.assertEqual(clone.source_url, self.seed.source_url)
        self.assertEqual(clone.category, self.seed.category)

    def test_active_seed_requires_subcategory(self):
        seed = DbHotlineSeed(
            key="top-level-gaming",
            source_url="https://hotline.ua/ua/game/",
            category=self.parent_category,
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            seed.full_clean()

    @patch("parsing.admin.queue_hotline_seed_refresh")
    def test_save_model_queues_active_subcategory_source_after_commit(self, mocked_queue):
        seed = DbHotlineSeed(
            key="new-gamepads",
            source_url="https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/",
            category=self.category,
            is_active=True,
        )
        form = Mock(changed_data=["source_url"])

        with self.captureOnCommitCallbacks(execute=True):
            self.model_admin.save_model(self.request, seed, form, change=False)

        mocked_queue.assert_called_once_with(seed.pk)


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

    def test_search_category_fetches_pages_until_no_new_products(self):
        html = (FIXTURE_DIR / "hotline_search_nuxt.html").read_text(encoding="utf-8")
        empty_response = Mock(text="<html><script>window.__NUXT__={}</script></html>")
        empty_response.raise_for_status.return_value = None
        first_response = Mock(text=html)
        first_response.raise_for_status.return_value = None
        second_response = Mock(text=html)
        second_response.raise_for_status.return_value = None
        client = Mock()
        client.get.side_effect = [first_response, second_response, empty_response]

        offers = HotlineAdapter(client=client).search_category(
            "https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/",
            max_pages=3,
        )

        self.assertEqual(len(offers), 2)
        self.assertEqual(
            client.get.call_args_list[0].args[0],
            "https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/",
        )
        self.assertEqual(
            client.get.call_args_list[1].args[0],
            "https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/?p=2",
        )
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
        self.assertEqual(offers[0].seller_logo_url, "https://hotline.ua/img/shops/gro-logo.png")
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

    @patch("parsing.services.httpx.Client")
    def test_upsert_hotline_gift_caches_image_in_default_storage(self, mocked_client):
        product_response = Mock()
        product_response.text = (
            '<meta property="og:image" '
            'content="https://hotline.ua/img/real-gamepad.jpg">'
        )
        product_response.raise_for_status.return_value = None
        image_response = Mock()
        image_response.headers = {"content-type": "image/jpeg"}
        image_response.content = b"jpeg-bytes"
        image_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            product_response,
            image_response,
        ]

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                gift, created, changed = upsert_hotline_gift_from_summary(
                    self._seed(),
                    self._summary(),
                )

                self.assertTrue(created)
                self.assertTrue(changed)
                self.assertEqual(gift.image_url, "https://hotline.ua/img/gamepad.jpg")
                self.assertEqual(gift.image.name, "gifts/hotline/659422.jpg")
                self.assertTrue(default_storage.exists(gift.image.name))
                with default_storage.open(gift.image.name, "rb") as cached_file:
                    self.assertEqual(cached_file.read(), b"jpeg-bytes")

        mocked_client.return_value.__enter__.return_value.get.assert_any_call(
            "https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/659422/",
        )
        mocked_client.return_value.__enter__.return_value.get.assert_any_call(
            "https://hotline.ua/img/real-gamepad.jpg",
        )

    @patch("parsing.services.httpx.Client")
    def test_upsert_hotline_gift_assigns_basic_tags(self, mocked_client):
        product_response = Mock()
        product_response.text = (
            '<meta property="og:image" '
            'content="https://hotline.ua/img/real-gamepad.jpg">'
        )
        product_response.raise_for_status.return_value = None
        image_response = Mock()
        image_response.headers = {"content-type": "image/jpeg"}
        image_response.content = b"jpeg-bytes"
        image_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            product_response,
            image_response,
        ]

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                gift, _, _ = upsert_hotline_gift_from_summary(
                    self._seed(),
                    self._summary(),
                )

        tag_names = set(gift.tags.values_list("name", flat=True))
        self.assertIn("Gaming", tag_names)
        self.assertIn("Teen", tag_names)

    @patch("parsing.services.httpx.Client")
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

    @patch("parsing.services.httpx.Client")
    def test_cache_hotline_images_command_caches_existing_image_urls(self, mocked_client):
        product_response = Mock()
        product_response.text = (
            '<meta property="og:image" '
            'content="https://hotline.ua/img/existing-real.jpg">'
        )
        product_response.raise_for_status.return_value = None
        image_response = Mock()
        image_response.headers = {"content-type": "image/jpeg"}
        image_response.content = b"image-bytes"
        image_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            product_response,
            image_response,
        ]

        gift = Gift.objects.create(
            name="Existing Hotline Gift",
            slug="existing-hotline-gift",
            gender="U",
            age_min=0,
            age_max=100,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="12345",
            source_product_url="https://hotline.ua/ua/product/existing/",
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

    @patch("parsing.services.httpx.Client")
    def test_upsert_hotline_gift_skips_small_placeholder_gif(self, mocked_client):
        product_response = Mock()
        product_response.text = ""
        product_response.raise_for_status.return_value = None
        localized_product_response = Mock()
        localized_product_response.text = ""
        localized_product_response.raise_for_status.return_value = None
        placeholder_response = Mock()
        placeholder_response.headers = {"content-type": "image/gif"}
        placeholder_response.content = b"0" * 3009
        placeholder_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            product_response,
            localized_product_response,
            placeholder_response,
        ]

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

    @patch("parsing.services.httpx.Client")
    def test_upsert_hotline_gift_tries_neighbor_image_when_search_image_is_placeholder(self, mocked_client):
        product_response = Mock()
        product_response.text = ""
        product_response.raise_for_status.return_value = None
        localized_product_response = Mock()
        localized_product_response.text = ""
        localized_product_response.raise_for_status.return_value = None
        placeholder_response = Mock()
        placeholder_response.headers = {"content-type": "image/gif"}
        placeholder_response.content = b"0" * 3009
        placeholder_response.raise_for_status.return_value = None
        real_image_response = Mock()
        real_image_response.headers = {"content-type": "image/jpeg"}
        real_image_response.content = b"real-jpeg-bytes"
        real_image_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            product_response,
            localized_product_response,
            placeholder_response,
            real_image_response,
        ]

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                gift, created, changed = upsert_hotline_gift_from_summary(
                    self._seed(),
                    self._summary(image_url="https://hotline.ua/img/tx/571/5714896300.jpg"),
                )

                self.assertTrue(created)
                self.assertTrue(changed)
                self.assertEqual(gift.image.name, "gifts/hotline/659422.jpg")
                with default_storage.open(gift.image.name, "rb") as cached_file:
                    self.assertEqual(cached_file.read(), b"real-jpeg-bytes")

        mocked_client.return_value.__enter__.return_value.get.assert_any_call(
            "https://hotline.ua/img/tx/571/5714896305.jpg",
        )

    @patch("parsing.services.httpx.Client")
    def test_resolve_hotline_product_image_url_tries_ua_path_variant(self, mocked_client):
        not_found_response = Mock()
        not_found_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=Mock(),
            response=Mock(status_code=404),
        )
        product_response = Mock()
        product_response.text = (
            '<meta property="og:image" '
            'content="https://hotline.ua/img/real-from-ua.jpg">'
        )
        product_response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.side_effect = [
            not_found_response,
            product_response,
        ]

        resolved_url = resolve_hotline_product_image_url(
            "https://hotline.ua/computer-gejmpady-dzhojstiki-ruli/logitech-gamepad-f310/",
            "https://hotline.ua/img/placeholder.jpg",
        )

        self.assertEqual(resolved_url, "https://hotline.ua/img/real-from-ua.jpg")
        mocked_client.return_value.__enter__.return_value.get.assert_any_call(
            "https://hotline.ua/computer-gejmpady-dzhojstiki-ruli/logitech-gamepad-f310/",
        )
        mocked_client.return_value.__enter__.return_value.get.assert_any_call(
            "https://hotline.ua/ua/computer-gejmpady-dzhojstiki-ruli/logitech-gamepad-f310/",
        )

    def test_extract_hotline_og_image_url_accepts_content_before_property(self):
        html = '<meta content="https://hotline.ua/img/real.jpg" property="og:image">'

        self.assertEqual(
            extract_hotline_og_image_url(html),
            "https://hotline.ua/img/real.jpg",
        )

    @patch("parsing.services.httpx.Client")
    def test_cache_shop_logo_uses_explicit_logo_url(self, mocked_client):
        response = Mock()
        response.headers = {"content-type": "image/png"}
        response.content = b"png-logo"
        response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.return_value = response

        shop = Shop.objects.create(
            name="Logo Shop",
            slug="logo-shop",
            website="https://logo-shop.example",
            shop_type="specialized",
        )

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                cached = cache_shop_logo(shop, "https://hotline.ua/img/shops/logo.png")
                shop.save(update_fields=["logo"])

                self.assertTrue(cached)
                self.assertEqual(shop.logo.name, "shops/static/images/hotline/logo-shop.png")
                self.assertTrue(default_storage.exists(shop.logo.name))
                with default_storage.open(shop.logo.name, "rb") as logo_file:
                    self.assertEqual(logo_file.read(), b"png-logo")

    @patch("parsing.services.httpx.Client")
    def test_cache_shop_logos_command_caches_favicon(self, mocked_client):
        response = Mock()
        response.headers = {"content-type": "image/x-icon"}
        response.content = b"ico-logo"
        response.raise_for_status.return_value = None
        mocked_client.return_value.__enter__.return_value.get.return_value = response

        shop = Shop.objects.create(
            name="Favicon Shop",
            slug="favicon-shop",
            website="https://favicon-shop.example/catalog",
            shop_type="specialized",
        )

        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                output = StringIO()
                call_command("cache_shop_logos", stdout=output)

                shop.refresh_from_db()
                self.assertEqual(shop.logo.name, "shops/static/images/hotline/favicon-shop.ico")
                self.assertTrue(default_storage.exists(shop.logo.name))
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

    @patch("parsing.tasks.HotlineAdapter.fetch_product_offers")
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
