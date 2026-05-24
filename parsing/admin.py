from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode

from .models import HotlineSeed, IngestionRun
from .seed_catalog import import_hotline_category_templates
from .tasks import active_hotline_seeds, queue_hotline_seed_refresh


def queue_run_again(run: IngestionRun) -> bool:
    if run.task_type == IngestionRun.TASK_TYPE_SEED_REFRESH and run.seed_key:
        try:
            return queue_hotline_seed_refresh(run.seed_key) is not None
        except HotlineSeed.DoesNotExist:
            return False

    return False


@admin.register(HotlineSeed)
class HotlineSeedAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "source_url",
        "query",
        "category",
        "is_active",
        "priority",
        "last_queued_at",
        "related_runs_link",
    ]
    list_filter = ["is_active", "category"]
    search_fields = ["key", "query", "source_url", "category__name"]
    autocomplete_fields = ["category"]
    readonly_fields = ["last_queued_at", "created_at", "updated_at", "related_runs_link"]
    list_editable = ["is_active", "priority"]
    ordering = ["priority", "key"]
    actions = [
        "queue_selected_seeds",
        "make_active",
        "make_inactive",
        "clone_selected_seeds",
        "import_default_category_templates",
    ]

    fieldsets = (
        (None, {"fields": ("key", "source_url", "query", "category")}),
        ("Scheduling", {"fields": ("is_active", "priority", "last_queued_at")}),
        ("History", {"fields": ("related_runs_link", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        should_queue = obj.is_active and (
            not change
            or any(field in form.changed_data for field in ("is_active", "source_url", "query", "category"))
        )
        super().save_model(request, obj, form, change)
        if should_queue:
            transaction.on_commit(lambda: queue_hotline_seed_refresh(obj.pk))

    @admin.action(description="Queue selected Hotline seeds")
    def queue_selected_seeds(self, request, queryset):
        queued = 0
        skipped = 0
        for seed in queryset.select_related("category"):
            if not seed.is_active or seed.category.parent_id is None:
                skipped += 1
                continue
            run = queue_hotline_seed_refresh(seed)
            if run is None:
                skipped += 1
            else:
                queued += 1

        self.message_user(
            request,
            f"Queued or reused {queued} Hotline seed run(s). Skipped {skipped}.",
            messages.INFO,
        )

    @admin.action(description="Make selected seeds active")
    def make_active(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} Hotline seed(s) made active.", messages.INFO)

    @admin.action(description="Make selected seeds inactive")
    def make_inactive(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} Hotline seed(s) made inactive.", messages.INFO)

    @admin.action(description="Clone selected seeds")
    def clone_selected_seeds(self, request, queryset):
        cloned = 0
        for seed in queryset.select_related("category"):
            base_key = f"{seed.key}-copy"
            key = base_key
            counter = 2
            while HotlineSeed.objects.filter(key=key).exists():
                key = f"{base_key}-{counter}"
                counter += 1
            HotlineSeed.objects.create(
                key=key,
                query=seed.query,
                source_url=seed.source_url,
                category=seed.category,
                is_active=False,
                priority=seed.priority,
            )
            cloned += 1

        self.message_user(request, f"Cloned {cloned} Hotline seed(s) as inactive.", messages.INFO)

    @admin.action(description="Import default Hotline category templates")
    def import_default_category_templates(self, request, queryset):
        created, updated = import_hotline_category_templates()
        self.message_user(
            request,
            f"Imported Hotline category templates: created {created}, updated {updated}.",
            messages.INFO,
        )

    def related_runs_link(self, obj):
        if obj.pk is None:
            return "-"
        url = (
            reverse("admin:search_ingestionrun_changelist")
            + "?"
            + urlencode({"seed_key": obj.key})
        )
        return format_html('<a href="{}">View ingestion runs</a>', url)

    related_runs_link.short_description = "Ingestion runs"


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
        queued = 0
        skipped = 0
        for seed in active_hotline_seeds():
            run = queue_hotline_seed_refresh(seed)
            if run is None:
                skipped += 1
            else:
                queued += 1

        self.message_user(
            request,
            f"Queued or reused {queued} active Hotline seed refresh run(s). Skipped {skipped}.",
            messages.INFO,
        )
