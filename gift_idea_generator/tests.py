from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .settings import (
    MEDIA_ROOT,
    MEDIA_URL,
    build_default_storage_config,
    build_s3_media_storage_options,
)


class MediaStorageSettingsTestCase(SimpleTestCase):
    def test_default_storage_uses_local_filesystem(self):
        config = build_default_storage_config({})

        self.assertEqual(
            config["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )
        self.assertEqual(config["OPTIONS"]["location"], MEDIA_ROOT)
        self.assertEqual(config["OPTIONS"]["base_url"], MEDIA_URL)

    def test_s3_storage_uses_provider_neutral_media_env(self):
        config = build_default_storage_config(
            {
                "MEDIA_STORAGE_BACKEND": "s3",
                "MEDIA_S3_BUCKET_NAME": "giftflow-media",
                "MEDIA_S3_ACCESS_KEY_ID": "access-key",
                "MEDIA_S3_SECRET_ACCESS_KEY": "secret-key",
                "MEDIA_S3_REGION_NAME": "auto",
                "MEDIA_S3_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
                "MEDIA_S3_CUSTOM_DOMAIN": "media.example.com",
                "MEDIA_S3_LOCATION": "/media/",
            },
        )

        self.assertEqual(config["BACKEND"], "storages.backends.s3.S3Storage")
        self.assertEqual(config["OPTIONS"]["bucket_name"], "giftflow-media")
        self.assertEqual(config["OPTIONS"]["access_key"], "access-key")
        self.assertEqual(config["OPTIONS"]["secret_key"], "secret-key")
        self.assertEqual(config["OPTIONS"]["region_name"], "auto")
        self.assertEqual(
            config["OPTIONS"]["endpoint_url"],
            "https://account.r2.cloudflarestorage.com",
        )
        self.assertEqual(config["OPTIONS"]["custom_domain"], "media.example.com")
        self.assertEqual(config["OPTIONS"]["location"], "media")
        self.assertFalse(config["OPTIONS"]["querystring_auth"])
        self.assertFalse(config["OPTIONS"]["file_overwrite"])
        self.assertEqual(
            config["OPTIONS"]["object_parameters"],
            {"CacheControl": "max-age=86400"},
        )

    def test_s3_storage_requires_credentials(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "MEDIA_S3_BUCKET_NAME",
        ):
            build_s3_media_storage_options({})

    def test_unknown_media_storage_backend_is_rejected(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "MEDIA_STORAGE_BACKEND must be either 'local' or 's3'",
        ):
            build_default_storage_config({"MEDIA_STORAGE_BACKEND": "database"})
