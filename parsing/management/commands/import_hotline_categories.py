from django.core.management.base import BaseCommand

from parsing.seed_catalog import import_hotline_category_templates
from parsing.seeds import HOTLINE_SEED_TEMPLATES


class Command(BaseCommand):
    help = "Import the default Hotline category tree and subcategory sources."

    def handle(self, *args, **options):
        created, updated = import_hotline_category_templates()
        self.stdout.write(
            self.style.SUCCESS(
                "Imported Hotline categories. "
                f"Created {created}, updated {updated}, "
                f"template total {len(HOTLINE_SEED_TEMPLATES)}.",
            ),
        )
