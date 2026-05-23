from django.core.management.base import BaseCommand

from gifts.default_tags import DEFAULT_TAGS, seed_default_tags


class Command(BaseCommand):
    help = "Seed default gift tags used by search filters and catalog ingestion."

    def handle(self, *args, **options):
        created = seed_default_tags()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded tags. Created {created}, total default set {len(DEFAULT_TAGS)}.",
            ),
        )
