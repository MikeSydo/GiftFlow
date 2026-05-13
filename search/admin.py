from django.contrib import admin

from .models import IngestionRun, SearchIngestionJob


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

    def has_add_permission(self, request):
        return False


@admin.register(SearchIngestionJob)
class SearchIngestionJobAdmin(admin.ModelAdmin):
    list_display = [
        "source",
        "normalized_query",
        "status",
        "result_count",
        "gift_count",
        "updated_at",
        "finished_at",
    ]
    list_filter = ["source", "status", "created_at", "updated_at"]
    search_fields = ["query", "normalized_query", "last_error"]
    readonly_fields = [
        "source",
        "query",
        "normalized_query",
        "request_filters",
        "status",
        "result_count",
        "gift_count",
        "last_error",
        "last_requested_at",
        "queued_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "gift_list",
    ]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Matched gifts")
    def gift_list(self, obj):
        names = list(obj.gifts.values_list("name", flat=True)[:20])
        if not names:
            return "-"
        if obj.gifts.count() > 20:
            names.append("...")
        return ", ".join(names)
