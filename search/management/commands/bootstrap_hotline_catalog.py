from django.core.management.base import BaseCommand

from search.seeds import HOTLINE_SEEDS
from search.tasks import queue_hotline_seed_refresh


class Command(BaseCommand):
    help = "Queue Hotline seed refresh tasks for initial catalog bootstrap."

    def handle(self, *args, **options):
        queued = 0
        for seed in HOTLINE_SEEDS:
            queue_hotline_seed_refresh(seed.key)
            queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {queued} Hotline seed refresh task(s).",
            ),
        )
