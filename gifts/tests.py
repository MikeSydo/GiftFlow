from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError

from django.test import TestCase, override_settings
from django.urls import reverse

from gifts.models import Category, Gift, Tag


class FakeS3Object:
    def __init__(self, key):
        self.key = key


class FakeS3Objects:
    def __init__(self, keys):
        self.keys = keys

    def filter(self, *, Prefix):
        return [FakeS3Object(key) for key in self.keys if key.startswith(Prefix)]


class FakeS3Bucket:
    def __init__(self, keys):
        self.objects = FakeS3Objects(keys)


class FakeS3Storage:
    __module__ = "storages.backends.s3"

    def __init__(self, keys, location="media"):
        self.location = location
        self.bucket = FakeS3Bucket(keys)
        self.deleted_names = []

    def delete(self, name):
        self.deleted_names.append(name)


class ObsoleteGiftCategorySeedCommandTestCase(TestCase):
    def test_seed_gift_categories_command_is_removed(self):
        with self.assertRaises(CommandError):
            call_command("seed_gift_categories")


class SeedGiftTagsCommandTestCase(TestCase):
    def test_seed_command_is_idempotent(self):
        output = StringIO()

        call_command("seed_gift_tags", stdout=output)
        first_count = Tag.objects.count()
        call_command("seed_gift_tags", stdout=output)
        second_count = Tag.objects.count()

        self.assertEqual(first_count, 23)
        self.assertEqual(second_count, 23)
        self.assertTrue(Tag.objects.filter(name="Tech", tag_type="I").exists())
        self.assertTrue(Tag.objects.filter(name="Birthday", tag_type="O").exists())


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

            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("copied=1", output.getvalue())
            self.assertEqual(
                (Path(destination_root) / self.gift.image.name).read_bytes(),
                b"gift",
            )

    def test_copy_media_to_storage_reports_missing_files(self):
        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("missing=1", output.getvalue())
            self.assertFalse((Path(destination_root) / self.gift.image.name).exists())

    def test_copy_media_to_storage_skips_empty_image_fields(self):
        self.gift.image = ""
        self.gift.save(update_fields=["image"])

        with TemporaryDirectory() as source_root, TemporaryDirectory() as destination_root:
            with override_settings(
                MEDIA_ROOT=source_root,
                STORAGES=self._storage_settings(destination_root),
            ):
                output = StringIO()
                call_command("copy_media_to_storage", stdout=output)

            self.assertIn("copied=0", output.getvalue())
            self.assertIn("missing=0", output.getvalue())


class PurgeMediaStorageCommandTestCase(TestCase):
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

    def test_refuses_non_s3_default_storage(self):
        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                with self.assertRaises(CommandError):
                    call_command("purge_media_storage", "--dry-run")

    def test_requires_confirm_when_not_dry_run(self):
        storage = FakeS3Storage(["media/gifts/a.jpg"])

        with patch("gifts.management.commands.purge_media_storage.default_storage", storage):
            with self.assertRaises(CommandError):
                call_command("purge_media_storage")

        self.assertEqual(storage.deleted_names, [])

    def test_dry_run_scopes_to_configured_media_prefix(self):
        storage = FakeS3Storage(
            [
                "media/gifts/a.jpg",
                "media/shops/logo.png",
                "other/gifts/b.jpg",
            ],
        )

        with patch("gifts.management.commands.purge_media_storage.default_storage", storage):
            output = StringIO()
            call_command("purge_media_storage", "--dry-run", stdout=output)

        value = output.getvalue()
        self.assertIn("Would delete: media/gifts/a.jpg", value)
        self.assertIn("Would delete: media/shops/logo.png", value)
        self.assertNotIn("other/gifts/b.jpg", value)
        self.assertIn("would_delete=2", value)
        self.assertEqual(storage.deleted_names, [])

    def test_confirm_deletes_storage_relative_names(self):
        storage = FakeS3Storage(
            [
                "media/gifts/a.jpg",
                "media/shops/logo.png",
                "other/gifts/b.jpg",
            ],
        )

        with patch("gifts.management.commands.purge_media_storage.default_storage", storage):
            output = StringIO()
            call_command("purge_media_storage", "--confirm", stdout=output)

        self.assertEqual(
            storage.deleted_names,
            ["gifts/a.jpg", "shops/logo.png"],
        )
        self.assertIn("deleted=2", output.getvalue())

    def test_refuses_empty_storage_location(self):
        storage = FakeS3Storage(["gifts/a.jpg"], location="")

        with patch("gifts.management.commands.purge_media_storage.default_storage", storage):
            with self.assertRaises(CommandError):
                call_command("purge_media_storage", "--dry-run")


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

    def test_deleting_gift_removes_primary_file(self):
        with TemporaryDirectory() as destination_root:
            with override_settings(STORAGES=self._storage_settings(destination_root)):
                image_name = self._save_file("gifts/delete-primary.jpg")
                gift = Gift.objects.create(
                    name="Delete Gift",
                    slug="delete-gift",
                    category=self.category,
                    gender="U",
                    age_min=0,
                    age_max=100,
                    image=image_name,
                )

                gift.delete()

                self.assertFalse(default_storage.exists(image_name))

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
        self.parent_category = Category.objects.create(
            name="Gaming",
            slug="gaming",
            is_active=True,
        )
        self.category = Category.objects.create(
            name="Handheld consoles",
            slug="handheld-consoles",
            parent=self.parent_category,
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

    def test_gift_detail_renders_gift_without_offers(self):
        response = self.client.get(reverse("gifts:gift_detail", args=[self.gift.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.gift.name)
        self.assertNotContains(response, "Stores and offers")


class CategoryNestedURLTestCase(TestCase):
    def setUp(self):
        self.parent = Category.objects.create(name="Gaming", slug="gaming")
        self.subcategory = Category.objects.create(
            name="Gamepads",
            slug="gamepads",
            parent=self.parent,
        )
        self.gift = Gift.objects.create(
            name="Controller",
            slug="controller",
            category=self.subcategory,
            gender="U",
            age_min=0,
            age_max=100,
            is_active=True,
        )

    def test_parent_category_url_lists_subcategory_gifts(self):
        response = self.client.get(
            reverse("category_parent_detail", kwargs={"parent_slug": self.parent.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.gift.name)
        self.assertEqual(response.context["current_top_category"], self.parent)

    def test_parent_category_url_deduplicates_subcategory_names(self):
        Category.objects.create(
            name="Samsung",
            slug="samsung-tv",
            parent=self.parent,
            is_active=True,
        )
        Category.objects.create(
            name="Samsung",
            slug="samsung-headphones",
            parent=self.parent,
            is_active=True,
        )

        response = self.client.get(
            reverse("category_parent_detail", kwargs={"parent_slug": self.parent.slug}),
        )

        subcategory_names = [category.name for category in response.context["subcategories"]]
        self.assertEqual(subcategory_names.count("Samsung"), 1)

    def test_subcategory_url_lists_only_subcategory_gifts(self):
        response = self.client.get(
            reverse(
                "category_subcategory_detail",
                kwargs={
                    "parent_slug": self.parent.slug,
                    "subcategory_slug": self.subcategory.slug,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.gift.name)
        self.assertEqual(response.context["parent_category"], self.parent)

    def test_legacy_category_url_redirects_to_nested_url(self):
        response = self.client.get(
            reverse("gifts:category_detail", args=[self.subcategory.slug]),
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self.subcategory.get_absolute_url())
