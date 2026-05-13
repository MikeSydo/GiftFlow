from django.contrib import admin

from .models import (
    PriceHistory,
    ProductLink,
    Shop,
    ShopCategoryAlias,
    ShopClick,
    ShopIntegration,
    ShopSource,
)


class ShopSourceInline(admin.TabularInline):
    model = ShopSource
    extra = 0
    fields = ("discovery_mode", "source_type", "value", "priority", "is_active")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = [
        "name", "shop_type", "priority", "is_active",
        "total_products", "click_count", "has_affiliate",
    ]
    list_filter = ["shop_type", "is_active", "has_affiliate"]
    search_fields = ["name", "website"]
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ["priority", "is_active"]


@admin.register(ShopIntegration)
class ShopIntegrationAdmin(admin.ModelAdmin):
    list_display = [
        "shop", "connector_type", "base_url", "priority", "is_active",
    ]
    list_filter = ["connector_type", "is_active"]
    search_fields = ["shop__name", "base_url"]
    list_editable = ["priority", "is_active"]
    raw_id_fields = ["shop"]
    readonly_fields = [
        "shop", "connector_type", "base_url", "auth_type", "auth_config",
        "request_config", "field_mapping", "is_active", "priority",
        "created_at", "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShopSource)
class ShopSourceAdmin(admin.ModelAdmin):
    list_display = [
        "integration", "discovery_mode", "source_type", "priority", "is_active", "value",
    ]
    list_filter = ["discovery_mode", "source_type", "is_active", "integration__connector_type"]
    search_fields = ["integration__shop__name", "value"]
    list_editable = ["priority", "is_active"]
    raw_id_fields = ["integration"]
    readonly_fields = [
        "integration", "discovery_mode", "source_type", "value", "config",
        "priority", "is_active", "created_at", "updated_at",
    ]
    fieldsets = (
        (
            "Legacy source configuration",
            {
                "fields": (
                    "integration", "discovery_mode", "source_type", "value", "config",
                    "priority", "is_active",
                ),
                "description": (
                    "Legacy only. Active catalog ingestion is scheduled through Hotline seeds and "
                    "does not read ShopSource records."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShopCategoryAlias)
class ShopCategoryAliasAdmin(admin.ModelAdmin):
    list_display = [
        "shop", "raw_category", "normalized_category", "category", "status", "confidence",
    ]
    list_filter = ["shop", "status", "category"]
    search_fields = ["raw_category", "normalized_category", "shop__name"]
    raw_id_fields = ["shop", "category"]
    actions = ["mark_matched", "mark_ignored"]

    @admin.action(description="Mark selected aliases as matched")
    def mark_matched(self, request, queryset):
        updated = queryset.exclude(category__isnull=True).update(status=ShopCategoryAlias.STATUS_MATCHED)
        self.message_user(request, f"Marked {updated} aliases as matched")

    @admin.action(description="Mark selected aliases as ignored")
    def mark_ignored(self, request, queryset):
        updated = queryset.update(status=ShopCategoryAlias.STATUS_IGNORED)
        self.message_user(request, f"Marked {updated} aliases as ignored")


@admin.register(ProductLink)
class ProductLinkAdmin(admin.ModelAdmin):
    list_display = [
        "product_name", "shop", "seller_name", "gift", "price",
        "in_stock", "is_marketplace_offer", "needs_category_review",
        "category_confidence", "last_price_update", "click_count",
    ]
    list_filter = [
        "shop", "in_stock", "is_verified", "needs_category_review", "is_marketplace_offer",
    ]
    search_fields = [
        "product_name", "sku", "product_url", "original_category_name",
        "seller_name", "external_offer_id", "external_product_id",
    ]
    raw_id_fields = ["gift", "shop", "discovered_via_source"]
    readonly_fields = [
        "click_count", "last_checked", "last_price_update",
        "category_confidence", "image_url", "normalized_product_url",
    ]
    list_editable = ["needs_category_review"]
    actions = ["trigger_price_update", "verify_links", "approve_categories"]

    @admin.action(description="Update prices for selected offers")
    def trigger_price_update(self, request, queryset):
        from .tasks import update_product_price

        for link in queryset:
            update_product_price.delay(link.id)
        self.message_user(request, f"Price update started for {queryset.count()} offers")

    @admin.action(description="Verify selected links")
    def verify_links(self, request, queryset):
        from .tasks import verify_product_link

        for link in queryset:
            verify_product_link.delay(link.id)
        self.message_user(request, f"Verification started for {queryset.count()} links")

    @admin.action(description="Approve categories (clear review flag)")
    def approve_categories(self, request, queryset):
        updated = queryset.filter(needs_category_review=True).update(needs_category_review=False)
        self.message_user(request, f"Category approved for {updated} offers")


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ["product_link", "price", "in_stock", "recorded_at"]
    list_filter = ["in_stock"]
    date_hierarchy = "recorded_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ShopClick)
class ShopClickAdmin(admin.ModelAdmin):
    list_display = ["product_link", "user", "session_key", "ip_address", "clicked_at"]
    list_filter = ["clicked_at"]
    date_hierarchy = "clicked_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
