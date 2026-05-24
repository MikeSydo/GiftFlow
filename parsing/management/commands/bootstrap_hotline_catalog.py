from django.core.management.base import BaseCommand

from parsing.tasks import active_hotline_seeds, queue_hotline_seed_refresh


class Command(BaseCommand):
    help = "Queue active admin-managed Hotline seed refresh tasks for initial catalog bootstrap."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of active Hotline seeds to queue.",
        )

    def handle(self, *args, **options):
        queued = 0
        limit = options["limit"]
        for seed in active_hotline_seeds():
            if limit is not None and queued >= limit:
                break
            run = queue_hotline_seed_refresh(seed)
            if run is not None:
                queued += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {queued} Hotline seed refresh task(s).",
            ),
        )
