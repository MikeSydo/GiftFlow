from django.contrib import admin

from .models import IngestionRun


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
