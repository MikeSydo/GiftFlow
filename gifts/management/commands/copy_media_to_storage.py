from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from gifts.models import Gift, GiftImage
from shops.models import Shop


MEDIA_FIELDS = (
    (Gift, "image"),
    (GiftImage, "image"),
    (Shop, "logo"),
)


class Command(BaseCommand):
    help = "Copy local media files from MEDIA_ROOT to the configured default storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be copied without writing to the target storage.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace files that already exist in the target storage.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]
        stats = {
            "copied": 0,
            "would_copy": 0,
            "skipped": 0,
            "missing": 0,
            "failed": 0,
        }

        for model, field_name in MEDIA_FIELDS:
            queryset = (
                model.objects.exclude(**{field_name: ""})
                .exclude(**{f"{field_name}__isnull": True})
                .order_by("id")
            )
            for obj in queryset.iterator():
                self._copy_field_file(
                    obj,
                    field_name,
                    dry_run=dry_run,
                    overwrite=overwrite,
                    stats=stats,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Media copy finished: "
                f"copied={stats['copied']}, "
                f"would_copy={stats['would_copy']}, "
                f"skipped={stats['skipped']}, "
                f"missing={stats['missing']}, "
                f"failed={stats['failed']}.",
            ),
        )

    def _copy_field_file(self, obj, field_name, *, dry_run, overwrite, stats):
        field_file = getattr(obj, field_name)
        name = field_file.name
        if not name:
            stats["skipped"] += 1
            return

        source_path = Path(settings.MEDIA_ROOT) / name
        if not source_path.is_file():
            stats["missing"] += 1
            self.stdout.write(f"Missing local file: {name}")
            return

        if self._default_storage_points_to_source(name, source_path):
            stats["skipped"] += 1
            self.stdout.write(f"Skipping local default storage file: {name}")
            return

        if default_storage.exists(name) and not overwrite:
            stats["skipped"] += 1
            self.stdout.write(f"Skipping existing target file: {name}")
            return

        if dry_run:
            stats["would_copy"] += 1
            self.stdout.write(f"Would copy: {name}")
            return

        try:
            if overwrite and default_storage.exists(name):
                default_storage.delete(name)
            with source_path.open("rb") as source_file:
                default_storage.save(name, File(source_file, name=name))
        except Exception as exc:
            stats["failed"] += 1
            self.stderr.write(f"Failed to copy {name}: {exc}")
            return

        stats["copied"] += 1
        self.stdout.write(f"Copied: {name}")

    @staticmethod
    def _default_storage_points_to_source(name, source_path):
        try:
            target_path = Path(default_storage.path(name))
        except (AttributeError, NotImplementedError):
            return False

        try:
            return target_path.resolve() == source_path.resolve()
        except OSError:
            return False
