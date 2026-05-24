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

from .hotline import HotlineAdapter, HotlineChallengeError, HotlineOffer
from .admin import HotlineSeedAdmin, IngestionRunAdmin
from .models import HotlineSeed as DbHotlineSeed, IngestionRun
from .seed_catalog import import_hotline_seed_templates
from .seeds import HOTLINE_SEEDS, HotlineSeed
from .services import (
    extract_hotline_og_image_url,
    resolve_hotline_product_image_url,
    upsert_hotline_gift_from_summary,
)
from .tasks import (
    queue_hotline_seed_refresh,
    queue_missing_hotline_seed_refreshes,
    hotline_seed_fallback_queries,
    hotline_seed_product_path_prefixes,
    filter_hotline_seed_summaries,
    refresh_hotline_seed,
)

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

    def test_requeue_selected_runs_skips_removed_product_refreshes(self):
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

        self.model_admin.message_user.assert_called_once()
        self.assertIn("Skipped 1", self.model_admin.message_user.call_args.args[1])

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

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    @override_settings(HOTLINE_SEARCH_SUGGESTION_FALLBACK=False)
    def test_challenge_failure_pauses_cold_start_queueing(self, mocked_delay):
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=HOTLINE_SEEDS[0].key,
            status=IngestionRun.STATUS_FAILED,
            last_error="Hotline returned a captcha/challenge page for search results.",
            finished_at=timezone.now(),
        )

        queued = queue_missing_hotline_seed_refreshes()

        self.assertEqual(queued, 0)
        mocked_delay.assert_not_called()

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    @override_settings(HOTLINE_SEARCH_SUGGESTION_FALLBACK=False)
    def test_challenge_failure_pauses_direct_seed_queueing(self, mocked_delay):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_FAILED,
            last_error="Hotline returned a captcha/challenge page for search results.",
            finished_at=timezone.now(),
        )

        run = queue_hotline_seed_refresh(seed)

        self.assertIsNone(run)
        mocked_delay.assert_not_called()

    @patch("parsing.tasks.refresh_hotline_seed.delay")
    def test_bootstrap_hotline_catalog_respects_limit(self, mocked_delay):
        output = StringIO()

        call_command("bootstrap_hotline_catalog", "--limit", "2", stdout=output)

        self.assertEqual(
            IngestionRun.objects.filter(
                task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
                status=IngestionRun.STATUS_PENDING,
            ).count(),
            2,
        )
        self.assertEqual(mocked_delay.call_count, 2)
        self.assertIn("Queued 2 Hotline seed refresh task(s).", output.getvalue())

    @patch("parsing.tasks.HotlineAdapter")
    @override_settings(HOTLINE_SEARCH_SUGGESTION_FALLBACK=False)
    def test_queued_seed_refresh_skips_http_during_challenge_cooldown(self, mocked_adapter):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_PENDING,
        )
        IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=HOTLINE_SEEDS[1].key,
            status=IngestionRun.STATUS_FAILED,
            last_error="Hotline returned a captcha/challenge page for search results.",
            finished_at=timezone.now(),
        )

        refresh_hotline_seed(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, IngestionRun.STATUS_FAILED)
        self.assertIn("cooldown is active", run.last_error)
        mocked_adapter.assert_not_called()

    @patch("parsing.services.cache_hotline_gift_image", return_value=False)
    @patch("parsing.tasks.HotlineAdapter")
    def test_seed_refresh_falls_back_to_json_rpc_suggestions_after_challenge(
        self,
        mocked_adapter,
        mocked_cache_image,
    ):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_PENDING,
        )
        adapter = mocked_adapter.return_value.__enter__.return_value
        adapter.search_category.side_effect = HotlineChallengeError(
            "Hotline returned a captcha/challenge page for search results.",
        )
        adapter.search_suggestions.return_value = [
            HotlineOffer(
                external_product_id="25806754",
                external_offer_id="hotline-product-25806754",
                title="Зарядна станція EcoFlow DELTA 3 EU-Version",
                product_url="https://hotline.ua/mobile-zaryadnye-stancii/ecoflow-delta-3-eu-version/",
                image_url="https://hotline.ua/img/tx/507/5070515241.jpg",
                price=None,
                needs_product_refresh=False,
            )
        ]
        adapter.enrich_product_prices.return_value = [
            HotlineOffer(
                external_product_id="25806754",
                external_offer_id="hotline-product-25806754",
                title="Зарядна станція EcoFlow DELTA 3 EU-Version",
                product_url="https://hotline.ua/mobile-zaryadnye-stancii/ecoflow-delta-3-eu-version/",
                image_url="https://hotline.ua/img/tx/507/5070515241.jpg",
                price=Decimal("23999"),
                original_price=Decimal("28999"),
                needs_product_refresh=False,
            )
        ]

        refresh_hotline_seed(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, IngestionRun.STATUS_COMPLETED)
        self.assertEqual(run.discovered_count, 1)
        self.assertEqual(run.updated_count, 1)
        gift = Gift.objects.get(source_product_id="25806754")
        self.assertEqual(gift.catalog_source, Gift.CATALOG_SOURCE_HOTLINE)
        self.assertEqual(gift.min_price, Decimal("23999"))
        self.assertEqual(gift.max_price, Decimal("28999"))
        self.assertFalse(
            IngestionRun.objects.filter(
                task_type=IngestionRun.TASK_TYPE_PRODUCT_REFRESH,
                source_product_id="25806754",
            ).exists()
        )
        mocked_cache_image.assert_not_called()
        adapter.enrich_product_prices.assert_called_once()

    def test_seed_fallback_queries_include_short_meaningful_query(self):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.query = "Ваги із синхронізацією зі смартфоном"
        seed.category.name = seed.query

        self.assertEqual(
            hotline_seed_fallback_queries(seed),
            [
                "Ваги із синхронізацією зі смартфоном",
                "Ваги",
                "Ваги синхронізацією",
            ],
        )

    def test_seed_product_path_prefixes_derive_hotline_product_url_shapes(self):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.source_url = "https://hotline.ua/ua/av/televizory/26206/"

        self.assertEqual(
            hotline_seed_product_path_prefixes(seed),
            ("/av-televizory/", "/av/televizory/"),
        )

    def test_filter_seed_summaries_rejects_cross_category_fallback_products(self):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.source_url = "https://hotline.ua/ua/av/televizory/26206/"
        television = HotlineOffer(
            external_product_id="25526866",
            external_offer_id="hotline-product-25526866",
            title="NanoCell телевізор LG 43NANO81",
            product_url="https://hotline.ua/av-televizory/lg-43nano81/",
            image_url=None,
            price=None,
            needs_product_refresh=False,
        )
        tire = HotlineOffer(
            external_product_id="302755",
            external_offer_id="hotline-product-302755",
            title="Всесезонні шини Matador MPS 400",
            product_url="https://hotline.ua/auto/avtoshiny-i-motoshiny/302755/",
            image_url=None,
            price=None,
            needs_product_refresh=False,
        )

        self.assertEqual(filter_hotline_seed_summaries(seed, [television, tire]), [television])

    @patch("parsing.services.cache_hotline_gift_image", return_value=False)
    @patch("parsing.tasks.HotlineAdapter")
    def test_seed_refresh_tries_shorter_json_rpc_fallback_query(
        self,
        mocked_adapter,
        mocked_cache_image,
    ):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.query = "Ваги із синхронізацією зі смартфоном"
        seed.source_url = "https://hotline.ua/ua/bt/vesy-napolnye/125724/"
        seed.category.name = seed.query
        seed.save(update_fields=["query", "source_url"])
        seed.category.save(update_fields=["name"])
        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_PENDING,
        )
        adapter = mocked_adapter.return_value.__enter__.return_value
        adapter.search_category.side_effect = HotlineChallengeError(
            "Hotline returned a captcha/challenge page for search results.",
        )
        adapter.search_suggestions.side_effect = [
            [],
            [
                HotlineOffer(
                    external_product_id="13477649",
                    external_offer_id="hotline-product-13477649",
                    title="Ваги підлогові електронні Xiaomi Mi Body Composition Scale S400 White",
                    product_url="https://hotline.ua/bt-vesy-napolnye/xiaomi-mi-body-composition-scale/",
                    image_url="https://hotline.ua/img/tx/309/3095555895.jpg",
                    price=None,
                    needs_product_refresh=False,
                )
            ],
        ]
        adapter.enrich_product_prices.side_effect = lambda summaries: summaries

        refresh_hotline_seed(run.id)

        self.assertEqual(adapter.search_suggestions.call_args_list[0].args[0], seed.query)
        self.assertEqual(adapter.search_suggestions.call_args_list[1].args[0], "Ваги")
        run.refresh_from_db()
        self.assertEqual(run.status, IngestionRun.STATUS_COMPLETED)
        self.assertTrue(Gift.objects.filter(source_product_id="13477649").exists())
        mocked_cache_image.assert_not_called()

    @patch("parsing.tasks.HotlineAdapter")
    def test_seed_refresh_marks_unmatched_fallback_failed_without_raising(self, mocked_adapter):
        seed = DbHotlineSeed.objects.get(key=HOTLINE_SEEDS[0].key)
        seed.source_url = "https://hotline.ua/ua/av/televizory/26206/"
        seed.save(update_fields=["source_url"])
        run = IngestionRun.objects.create(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_PENDING,
        )
        adapter = mocked_adapter.return_value.__enter__.return_value
        adapter.search_category.side_effect = HotlineChallengeError(
            "Hotline returned a captcha/challenge page for search results.",
        )
        adapter.search_suggestions.return_value = [
            HotlineOffer(
                external_product_id="302755",
                external_offer_id="hotline-product-302755",
                title="Tire 400",
                product_url="https://hotline.ua/auto/avtoshiny-i-motoshiny/302755/",
                image_url=None,
                price=None,
                needs_product_refresh=False,
            )
        ]

        refresh_hotline_seed(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, IngestionRun.STATUS_FAILED)
        self.assertIn("matching seed category", run.last_error)
        self.assertFalse(Gift.objects.filter(source_product_id="302755").exists())

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

    def test_parse_search_html_raises_for_hotline_challenge(self):
        html = (FIXTURE_DIR / "hotline_challenge.html").read_text(encoding="utf-8")

        with self.assertRaises(HotlineChallengeError):
            HotlineAdapter()._parse_search_html(html)

    def test_parse_product_html_raises_for_hotline_challenge(self):
        html = (FIXTURE_DIR / "hotline_challenge.html").read_text(encoding="utf-8")

        with self.assertRaises(HotlineChallengeError):
            HotlineAdapter()._parse_product_html(html, external_product_id="21916104")

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

    def test_parse_search_suggestions_extracts_basic_product_summaries(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": [
                {
                    "id": 25806754,
                    "title": "Зарядна станція EcoFlow DELTA 3 EU-Version",
                    "url": "/mobile-zaryadnye-stancii/ecoflow-delta-3-eu-version/",
                    "imagePath": "/img/tx/507/5070515241.jpg",
                    "minPrice": 23999,
                    "maxPrice": 28999,
                    "currentEntity": "products",
                },
                {
                    "id": 1726,
                    "title": "Зарядні станції",
                    "url": "/mobile/zaryadnye-stancii/",
                    "currentEntity": "sections",
                },
            ],
        }

        offers = HotlineAdapter()._parse_search_suggestions(payload)

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].external_product_id, "25806754")
        self.assertEqual(
            offers[0].product_url,
            "https://hotline.ua/mobile-zaryadnye-stancii/ecoflow-delta-3-eu-version/",
        )
        self.assertEqual(offers[0].image_url, "https://hotline.ua/img/tx/507/5070515241.jpg")
        self.assertEqual(offers[0].price, Decimal("23999"))
        self.assertEqual(offers[0].original_price, Decimal("28999"))
        self.assertFalse(offers[0].needs_product_refresh)

    def test_parse_product_price_range_extracts_aggregate_offer(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Product","offers":{"@type":"AggregateOffer","lowPrice":10699,"highPrice":15754.21,"priceCurrency":"UAH"}}
        </script>
        """

        price, original_price = HotlineAdapter()._parse_product_price_range(html)

        self.assertEqual(price, Decimal("10699"))
        self.assertEqual(original_price, Decimal("15754.21"))

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

    @override_settings(HOTLINE_REQUEST_TOKEN="valid-token", HOTLINE_CITY_ID=188, HOTLINE_COOKIE_HEADER="hl_sid=1")
    def test_fetch_product_offers_graphql_extracts_merchant_offers(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "byPathQueryProduct": {
                    "id": "25526866",
                    "offers": {
                        "totalCount": 1,
                        "edges": [
                            {
                                "node": {
                                    "_id": "101",
                                    "conversionUrl": "/go/price/101/",
                                    "descriptionShort": "LG 43NANO81",
                                    "firmId": 77,
                                    "firmLogo": "/img/shops/logo.png",
                                    "firmTitle": "GRO",
                                    "firmExtraInfo": {"website": "gro.ua"},
                                    "price": 14999,
                                }
                            }
                        ],
                    },
                }
            }
        }
        client = Mock()
        client.post.return_value = response

        offers = HotlineAdapter(client=client).fetch_product_offers_graphql(
            product_path="av-televizory/lg-43nano81",
            product_url="https://hotline.ua/av-televizory/lg-43nano81/",
            external_product_id="25526866",
        )

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].external_offer_id, "101")
        self.assertEqual(offers[0].seller_name, "GRO")
        self.assertEqual(offers[0].seller_url, "https://gro.ua")
        self.assertEqual(offers[0].seller_logo_url, "https://hotline.ua/img/shops/logo.png")
        self.assertEqual(offers[0].price, Decimal("14999"))
        request = client.post.call_args
        self.assertEqual(request.kwargs["json"]["variables"]["path"], "av-televizory/lg-43nano81")
        self.assertEqual(request.kwargs["json"]["variables"]["cityId"], 188)
        self.assertEqual(request.kwargs["headers"]["x-token"], "valid-token")
        self.assertEqual(request.kwargs["headers"]["Cookie"], "hl_sid=1")

    @override_settings(HOTLINE_REQUEST_TOKEN="")
    def test_fetch_product_offers_graphql_requires_request_token(self):
        with self.assertRaises(HotlineChallengeError):
            HotlineAdapter(client=Mock()).fetch_product_offers_graphql(
                product_path="av-televizory/lg-43nano81",
                product_url="https://hotline.ua/av-televizory/lg-43nano81/",
                external_product_id="25526866",
            )

    @override_settings(HOTLINE_REQUEST_TOKEN="bad-token")
    def test_parse_product_offers_graphql_raises_for_invalid_token(self):
        payload = {
            "errors": [{"message": "invalid-request-token"}],
            "data": {"byPathQueryProduct": None},
        }

        with self.assertRaises(HotlineChallengeError):
            HotlineAdapter()._parse_product_offers_graphql(
                payload,
                external_product_id="25526866",
            )


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
    def test_upsert_hotline_gift_does_not_clear_existing_price_when_summary_has_no_price(self, mocked_client):
        mocked_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError(
            "network failed",
        )
        seed = self._seed()
        gift = Gift.objects.create(
            name="Gamepad Xbox Wireless",
            slug="gamepad-xbox-wireless",
            gender="U",
            age_min=0,
            age_max=100,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="659422",
            min_price=Decimal("2199"),
            max_price=Decimal("2599"),
        )
        summary = HotlineOffer(
            external_product_id="659422",
            external_offer_id="hotline-product-659422",
            title="Gamepad Xbox Wireless",
            product_url="https://hotline.ua/ua/computer/gejmpady-dzhojstiki-ruli/659422/",
            image_url=None,
            price=None,
            needs_product_refresh=False,
        )

        upserted, _, changed = upsert_hotline_gift_from_summary(seed, summary)

        self.assertEqual(upserted.id, gift.id)
        self.assertTrue(changed)
        upserted.refresh_from_db()
        self.assertEqual(upserted.min_price, Decimal("2199.00"))
        self.assertEqual(upserted.max_price, Decimal("2599.00"))

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
