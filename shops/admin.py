from django.contrib import admin

from .models import Shop, ProductLink, PriceHistory, ShopClick


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'shop_type', 'priority', 'is_active',
        'total_products', 'click_count', 'has_affiliate',
    ]
    list_filter = ['shop_type', 'is_active', 'has_affiliate']
    search_fields = ['name', 'website']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['priority', 'is_active']
    actions = ['trigger_discovery']

    @admin.action(description='Run Discovery for selected shops')
    def trigger_discovery(self, request, queryset):
        from .tasks import discover_shop_products
        from .scrapers import get_scraper

        count = 0
        for shop in queryset:
            scraper = get_scraper(shop.slug)
            if scraper is None:
                continue
            for url in scraper.get_category_urls():
                discover_shop_products.delay(shop.id, url)
            count += 1
        self.message_user(request, f'Discovery started for {count} shops')


@admin.register(ProductLink)
class ProductLinkAdmin(admin.ModelAdmin):
    list_display = [
        'product_name', 'shop', 'gift', 'price', 'in_stock',
        'is_verified', 'needs_category_review', 'category_confidence',
        'last_price_update', 'click_count',
    ]
    list_filter = ['shop', 'in_stock', 'is_verified', 'needs_category_review']
    search_fields = ['product_name', 'sku', 'product_url', 'original_category_name']
    raw_id_fields = ['gift', 'shop']
    readonly_fields = ['click_count', 'last_checked', 'last_price_update', 'category_confidence']
    list_editable = ['needs_category_review']
    actions = ['trigger_price_update', 'verify_links', 'approve_categories']

    @admin.action(description='Update prices for selected products')
    def trigger_price_update(self, request, queryset):
        from .tasks import update_product_price
        for link in queryset:
            update_product_price.delay(link.id)
        self.message_user(request, f'Price update started for {queryset.count()} products')

    @admin.action(description='Verify selected links')
    def verify_links(self, request, queryset):
        from .tasks import verify_product_link
        for link in queryset:
            verify_product_link.delay(link.id)
        self.message_user(request, f'Verification started for {queryset.count()} links')

    @admin.action(description='Approve categories (clear review flag)')
    def approve_categories(self, request, queryset):
        updated = queryset.filter(needs_category_review=True).update(needs_category_review=False)
        self.message_user(request, f'Category approved for {updated} products')


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ['product_link', 'price', 'in_stock', 'recorded_at']
    list_filter = ['in_stock']
    date_hierarchy = 'recorded_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ShopClick)
class ShopClickAdmin(admin.ModelAdmin):
    list_display = ['product_link', 'user', 'session_key', 'ip_address', 'clicked_at']
    list_filter = ['clicked_at']
    date_hierarchy = 'clicked_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
