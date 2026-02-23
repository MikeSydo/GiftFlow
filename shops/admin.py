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

    @admin.action(description='Запустити Discovery для обраних магазинів')
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
        self.message_user(request, f'Discovery запущено для {count} магазинів')


@admin.register(ProductLink)
class ProductLinkAdmin(admin.ModelAdmin):
    list_display = [
        'product_name', 'shop', 'gift', 'price', 'in_stock',
        'is_verified', 'last_price_update', 'click_count',
    ]
    list_filter = ['shop', 'in_stock', 'is_verified']
    search_fields = ['product_name', 'sku', 'product_url']
    raw_id_fields = ['gift', 'shop']
    readonly_fields = ['click_count', 'last_checked', 'last_price_update']
    actions = ['trigger_price_update', 'verify_links']

    @admin.action(description='Оновити ціни для обраних товарів')
    def trigger_price_update(self, request, queryset):
        from .tasks import update_product_price
        for link in queryset:
            update_product_price.delay(link.id)
        self.message_user(request, f'Оновлення цін запущено для {queryset.count()} товарів')

    @admin.action(description='Верифікувати обрані посилання')
    def verify_links(self, request, queryset):
        from .tasks import verify_product_link
        for link in queryset:
            verify_product_link.delay(link.id)
        self.message_user(request, f'Верифікацію запущено для {queryset.count()} посилань')


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
