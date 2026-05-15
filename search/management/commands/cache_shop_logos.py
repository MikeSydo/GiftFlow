from django.core.management.base import BaseCommand
from django.db.models import Q

from search.services import cache_shop_logo
from shops.models import Shop


class Command(BaseCommand):
    help = "Cache missing shop logos into active media storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report shops that would be processed without saving files.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of shops to process.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        queryset = Shop.objects.filter(Q(logo="") | Q(logo__isnull=True)).order_by("id")
        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        if dry_run:
            self.stdout.write(f"Would cache logos for {total} shop(s).")
            return

        cached = 0
        skipped = 0
        for shop in queryset.iterator():
            if cache_shop_logo(shop):
                shop.save(update_fields=["logo", "updated_at"])
                cached += 1
            else:
                skipped += 1

        self.stdout.write(
            f"Shop logo cache finished: total={total}, cached={cached}, skipped={skipped}."
        )
