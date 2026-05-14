from django.contrib import admin
from django.contrib import messages

from gifts.models import Gift

from .models import IngestionRun
from .seeds import HOTLINE_SEEDS
from .tasks import queue_hotline_product_refresh, queue_hotline_seed_refresh


def queue_run_again(run: IngestionRun) -> bool:
    if run.task_type == IngestionRun.TASK_TYPE_SEED_REFRESH and run.seed_key:
        queue_hotline_seed_refresh(run.seed_key)
        return True

    if run.task_type != IngestionRun.TASK_TYPE_PRODUCT_REFRESH:
        return False

    gift = run.gift
    if gift is None and run.source_product_id:
        gift = Gift.objects.filter(
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
            source_product_id=run.source_product_id,
        ).first()

    if gift is None or not gift.source_product_id or not gift.source_product_url:
        return False

    queue_hotline_product_refresh(gift)
    return True


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = [
        "task_type",
        "seed_key",
        "gift",
        "source_product_id",
        "status",
        "discovered_count",
        "updated_count",
        "updated_at",
        "finished_at",
    ]
    list_filter = ["task_type", "status", "created_at", "updated_at"]
    search_fields = ["seed_key", "source_product_id", "gift__name", "last_error"]
    readonly_fields = [
        "task_type",
        "seed_key",
        "gift",
        "source_product_id",
        "status",
        "discovered_count",
        "updated_count",
        "last_error",
        "queued_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ]
    actions = [
        "retry_failed_runs",
        "requeue_selected_runs",
        "queue_all_hotline_seed_refreshes",
    ]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Retry selected failed runs")
    def retry_failed_runs(self, request, queryset):
        queued = 0
        skipped = 0
        for run in queryset:
            if run.status != IngestionRun.STATUS_FAILED:
                skipped += 1
                continue
            if queue_run_again(run):
                queued += 1
            else:
                skipped += 1

        self.message_user(
            request,
            f"Queued or reused {queued} failed ingestion run(s). Skipped {skipped}.",
            messages.INFO,
        )

    @admin.action(description="Requeue selected seed/product refreshes")
    def requeue_selected_runs(self, request, queryset):
        queued = 0
        skipped = 0
        for run in queryset:
            if queue_run_again(run):
                queued += 1
            else:
                skipped += 1

        self.message_user(
            request,
            f"Queued or reused {queued} ingestion run(s). Skipped {skipped}.",
            messages.INFO,
        )

    @admin.action(description="Queue all Hotline seed refreshes")
    def queue_all_hotline_seed_refreshes(self, request, queryset):
        for seed in HOTLINE_SEEDS:
            queue_hotline_seed_refresh(seed.key)

        self.message_user(
            request,
            f"Queued or reused {len(HOTLINE_SEEDS)} Hotline seed refresh run(s).",
            messages.INFO,
        )
