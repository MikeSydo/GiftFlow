from django.core.management.base import BaseCommand

from search.seed_catalog import import_hotline_seed_templates
from search.seeds import HOTLINE_SEED_TEMPLATES


class Command(BaseCommand):
    help = "Import default Hotline seed templates into admin-managed database seeds."

    def handle(self, *args, **options):
        created, updated = import_hotline_seed_templates()
        self.stdout.write(
            self.style.SUCCESS(
                "Seeded Hotline seeds. "
                f"Created {created}, updated {updated}, "
                f"template total {len(HOTLINE_SEED_TEMPLATES)}.",
            ),
        )
