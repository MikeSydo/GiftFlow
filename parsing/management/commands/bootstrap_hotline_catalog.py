from django.core.management.base import BaseCommand

from parsing.tasks import active_hotline_seeds, queue_hotline_seed_refresh


class Command(BaseCommand):
    help = "Queue active admin-managed Hotline seed refresh tasks for initial catalog bootstrap."

    def handle(self, *args, **options):
        queued = 0
        for seed in active_hotline_seeds():
            queue_hotline_seed_refresh(seed)
            queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {queued} Hotline seed refresh task(s).",
            ),
        )
