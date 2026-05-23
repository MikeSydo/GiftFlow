from __future__ import annotations

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Purge media objects from the configured S3-compatible default storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List objects that would be deleted without deleting them.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete objects. Required unless --dry-run is used.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirm = options["confirm"]
        storage = default_storage
        location = getattr(storage, "location", "").strip("/")

        if not self._is_s3_storage(storage):
            raise CommandError("Default media storage is not S3-compatible; refusing to purge.")
        if not location:
            raise CommandError("MEDIA_S3_LOCATION must be non-empty to purge media safely.")
        if not dry_run and not confirm:
            raise CommandError("Use --dry-run first, then rerun with --confirm to delete media.")

        prefix = f"{location}/"
        objects = list(storage.bucket.objects.filter(Prefix=prefix))

        deleted = 0
        would_delete = 0
        failed = 0
        for obj in objects:
            key = obj.key
            relative_name = key[len(prefix):]
            if not relative_name:
                continue

            if dry_run:
                would_delete += 1
                self.stdout.write(f"Would delete: {key}")
                continue

            try:
                storage.delete(relative_name)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Failed to delete {key}: {exc}")
                continue

            deleted += 1
            self.stdout.write(f"Deleted: {key}")

        self.stdout.write(
            self.style.SUCCESS(
                "Media purge finished: "
                f"prefix={prefix}, "
                f"deleted={deleted}, "
                f"would_delete={would_delete}, "
                f"failed={failed}.",
            ),
        )

    @staticmethod
    def _is_s3_storage(storage) -> bool:
        module = storage.__class__.__module__
        return module.startswith("storages.backends.s3") and hasattr(storage, "bucket")
