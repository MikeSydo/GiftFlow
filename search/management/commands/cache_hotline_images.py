from django.core.management.base import BaseCommand

from gifts.models import Gift
from search.services import cache_hotline_gift_image


class Command(BaseCommand):
    help = "Cache existing gift image_url values into the configured media storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of gifts to process. Defaults to all matching gifts.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many gifts would be processed without downloading images.",
        )

    def handle(self, *args, **options):
        queryset = (
            Gift.objects.filter(catalog_source=Gift.CATALOG_SOURCE_HOTLINE)
            .exclude(source_product_id="")
            .exclude(image_url__isnull=True)
            .exclude(image_url="")
            .filter(image="")
            .order_by("id")
        )
        limit = options["limit"]
        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        if options["dry_run"]:
            self.stdout.write(f"Would cache images for {total} gift(s).")
            return

        cached = 0
        skipped = 0
        for gift in queryset.iterator():
            if cache_hotline_gift_image(
                gift,
                gift.image_url,
                product_url=gift.source_product_url,
            ):
                gift.save(update_fields=["image", "updated_at"])
                cached += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Image cache finished: total={total}, cached={cached}, skipped={skipped}.",
            ),
        )
