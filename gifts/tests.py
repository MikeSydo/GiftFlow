from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib import admin
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from decimal import Decimal

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from gifts.admin import GiftAdmin
from gifts.models import Category, Gift, GiftImage
from shops.models import ProductLink, Shop


class SeedGiftCategoriesCommandTestCase(TestCase):
    def test_seed_command_is_idempotent(self):
        output = StringIO()

        call_command("seed_gift_categories", stdout=output)
        first_count = Category.objects.count()
        call_command("seed_gift_categories", stdout=output)
        second_count = Category.objects.count()

        self.assertEqual(first_count, 16)
        self.assertEqual(second_count, 16)
        self.assertTrue(Category.objects.filter(slug="electronics").exists())
        self.assertTrue(Category.objects.filter(slug="home-kitchen").exists())


class CopyMediaToStorageCommandTestCase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="Media",
            slug="media",
            is_active=True,
        )
        self.gift = Gift.objects.create(
            name="Gift With Image",
            slug="gift-with-image",
            category=self.category,
            gender="U",
            age_min=0,
            age_max=100,
            image="gifts/example.jpg",
        )
        self.shop = Shop.objects.create(
            name="Shop With Logo",
            slug="shop-with-logo",
            website="https://shop.example",
            shop_type="specialized",
            logo="shops/static/images/logo.png",
        )
        self.gallery_image = GiftImage.objects.create(
            gift=self.gift,
            image="gifts/gallery/detail.jpg",
        )

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
    def _write_file(root, name, content=b"image-bytes"):
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_copy_media_to_storage_dry_run_does_not_copy_files(self):
        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            self._write_file(source_root, self.gift.image.name)

            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", "--dry-run", stdout=output)

            self.assertIn("would_copy=1", output.getvalue())
            self.assertFalse((Path(destination_root) / self.gift.image.name).exists())

    def test_copy_media_to_storage_copies_existing_local_files(self):
        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            self._write_file(source_root, self.gift.image.name, b"gift")
            self._write_file(source_root, self.shop.logo.name, b"logo")
            self._write_file(source_root, self.gallery_image.image.name, b"gallery")

            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("copied=3", output.getvalue())
            self.assertEqual(
                (Path(destination_root) / self.gift.image.name).read_bytes(),
                b"gift",
            )
            self.assertEqual(
                (Path(destination_root) / self.shop.logo.name).read_bytes(),
                b"logo",
            )
            self.assertEqual(
                (Path(destination_root) / self.gallery_image.image.name).read_bytes(),
                b"gallery",
            )

    def test_copy_media_to_storage_reports_missing_files(self):
        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("missing=3", output.getvalue())
            self.assertFalse((Path(destination_root) / self.gift.image.name).exists())

    def test_copy_media_to_storage_skips_empty_image_fields(self):
        self.gift.image = ""
        self.gift.save(update_fields=["image"])
        self.shop.logo = ""
        self.shop.save(update_fields=["logo"])
        self.gallery_image.delete()

        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("copied=0", output.getvalue())
            self.assertIn("missing=0", output.getvalue())


class MediaFileCleanupTestCase(TestCase):
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

    def setUp(self):
        self.category = Category.objects.create(
            name="Cleanup",
            slug="cleanup",
            is_active=True,
        )

    @staticmethod
    def _save_file(name, content=b"image"):
        return default_storage.save(name, ContentFile(content))

    def test_deleting_gift_removes_primary_and_gallery_files(self):
        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                image_name = self._save_file("gifts/delete-primary.jpg")
                gallery_name = self._save_file("gifts/gallery/delete-gallery.jpg")
                gift = Gift.objects.create(
                    name="Delete Gift",
                    slug="delete-gift",
                    category=self.category,
                    gender="U",
                    age_min=0,
                    age_max=100,
                    image=image_name,
                )
                GiftImage.objects.create(gift=gift, image=gallery_name)

                gift.delete()

                self.assertFalse(default_storage.exists(image_name))
                self.assertFalse(default_storage.exists(gallery_name))

    def test_replacing_gift_image_removes_old_file(self):
        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                old_name = self._save_file("gifts/old.jpg", b"old")
                new_name = self._save_file("gifts/new.jpg", b"new")
                gift = Gift.objects.create(
                    name="Replace Gift",
                    slug="replace-gift",
                    category=self.category,
                    gender="U",
                    age_min=0,
                    age_max=100,
                    image=old_name,
                )

                gift.image = new_name
                gift.save(update_fields=["image"])

                self.assertFalse(default_storage.exists(old_name))
                self.assertTrue(default_storage.exists(new_name))


class GiftDetailViewTestCase(TestCase):
    def setUp(self):
        self.cache_delay_patcher = patch("shops.tasks.update_gift_price_cache.delay")
        self.cache_delay_patcher.start()
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
            min_price=Decimal("19499.00"),
            max_price=Decimal("20599.00"),
            is_active=True,
        )
        self.shop_fast = Shop.objects.create(
            name="Fast Shop",
            slug="fast-shop",
            website="https://fast-shop.example",
            shop_type="specialized",
            priority=100,
        )
        self.shop_slow = Shop.objects.create(
            name="Slow Shop",
            slug="slow-shop",
            website="https://slow-shop.example",
            shop_type="specialized",
            priority=0,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_slow,
            product_url="https://hotline.ua/go/price/2/",
            product_name="Steam Deck 256 GB",
            price=Decimal("20599.00"),
            in_stock=True,
            external_offer_id="2",
            is_marketplace_offer=True,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_fast,
            product_url="https://hotline.ua/go/price/1/",
            product_name="Steam Deck 256 GB",
            price=Decimal("19499.00"),
            in_stock=True,
            external_offer_id="1",
            is_marketplace_offer=True,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_fast,
            product_url="https://hotline.ua/go/price/3/",
            product_name="Steam Deck 256 GB",
            price=Decimal("18000.00"),
            in_stock=False,
            external_offer_id="3",
            is_marketplace_offer=True,
        )

    def test_gift_detail_orders_offers_by_stock_then_price(self):
        response = self.client.get(reverse("gifts:gift_detail", args=[self.gift.slug]))

        self.assertEqual(response.status_code, 200)
        offers = list(response.context["offers"])
        self.assertEqual(
            [offer.external_offer_id for offer in offers],
            ["1", "2", "3"],
        )

    def test_gift_detail_uses_tracked_offer_click_urls(self):
        response = self.client.get(reverse("gifts:gift_detail", args=[self.gift.slug]))
        best_offer = ProductLink.objects.get(external_offer_id="1")

        self.assertContains(response, "data-offer-click")
        self.assertContains(
            response,
            reverse("shops:productlink-click", args=[best_offer.id]),
        )

    def test_gift_detail_renders_shop_logo_when_available(self):
        self.shop_fast.logo = "shops/static/images/hotline/fast-shop.png"
        self.shop_fast.save(update_fields=["logo"])

        response = self.client.get(reverse("gifts:gift_detail", args=[self.gift.slug]))

        self.assertContains(response, "offer-card__logo")
        self.assertContains(response, "/media/shops/static/images/hotline/fast-shop.png")


class GiftAdminActionTestCase(TestCase):
    def setUp(self):
        self.request = RequestFactory().post("/admin/gifts/gift/")
        self.model_admin = GiftAdmin(Gift, admin.site)
        self.model_admin.message_user = Mock()
        self.hotline_gift = Gift.objects.create(
            name="Hotline Gift",
            slug="hotline-gift",
            gender="U",
            age_min=0,
            age_max=100,
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id="21916104",
            source_product_url="https://hotline.ua/ua/computer-igrovye-pristavki/steam-deck-256-gb/",
        )
        self.manual_gift = Gift.objects.create(
            name="Manual Gift",
            slug="manual-gift",
            gender="U",
            age_min=0,
            age_max=100,
        )

    @patch("gifts.admin.queue_hotline_product_refresh")
    def test_refresh_hotline_offers_queues_only_hotline_gifts(self, mocked_queue):
        self.model_admin.refresh_hotline_offers(
            self.request,
            Gift.objects.filter(id__in=[self.hotline_gift.id, self.manual_gift.id]),
        )

        mocked_queue.assert_called_once_with(self.hotline_gift)
        self.model_admin.message_user.assert_called_once()
