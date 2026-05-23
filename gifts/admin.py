from django.contrib import admin
from django.contrib import messages
from django.utils.safestring import mark_safe

from .models import Category, Tag, Gift, GiftImage
from parsing.tasks import queue_hotline_product_refresh

class GiftImageInline(admin.TabularInline):
    model = GiftImage
    extra = 1
    fields = ('image', 'alt_text', 'order')
    ordering = ['order']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'order', 'is_active', 'icon')
    list_filter = ('is_active', 'parent')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['order', 'name']
    list_editable = ('order', 'is_active')

    fieldsets = (
        ('Main information', {
            'fields': ('name', 'slug', 'description')
        }),
        ('Settings', {
            'fields': ('parent', 'icon', 'order', 'is_active')
        }),
    )

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag_type', 'slug')
    list_filter = ('tag_type',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['tag_type', 'name']

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'tag_type')
        }),
    )

@admin.register(Gift)
class GiftAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'gender', 'age_range', 'price_range', 'popularity_score', 'is_active', 'is_featured', 'created_at')
    list_filter = ('is_active', 'is_featured', 'gender', 'category', 'tags')
    search_fields = ('name', 'description', 'short_description')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('tags',)
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    list_editable = ('is_active', 'is_featured')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [GiftImageInline]
    actions = ['make_active', 'make_inactive', 'refresh_hotline_offers']

    @admin.action(description='Make active')
    def make_active(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} gifts made active.')

    @admin.action(description='Make inactive')
    def make_inactive(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} gifts made inactive.')

    @admin.action(description='Refresh Hotline offers for selected gifts')
    def refresh_hotline_offers(self, request, queryset):
        queued = 0
        skipped = 0
        gifts = queryset.filter(
            catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
        ).exclude(
            source_product_id="",
        ).exclude(
            source_product_url="",
        )

        for gift in gifts:
            queue_hotline_product_refresh(gift)
            queued += 1

        skipped = queryset.count() - queued
        self.message_user(
            request,
            f"Queued or reused offer refreshes for {queued} Hotline gift(s). Skipped {skipped}.",
            messages.INFO,
        )

    fieldsets = (
        ('Main Information', {
            'fields': ('name', 'slug', 'short_description', 'description', 'image', 'image_url')
        }),
        ('Categories', {
            'fields': ('category', 'tags')
        }),
        ('Characteristics', {
            'fields': ('gender', 'age_min', 'age_max', 'min_price', 'max_price')
        }),
        ('Settings', {
            'fields': ('popularity_score', 'is_active', 'is_featured')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def age_range(self, obj):
        return f"{obj.age_min}-{obj.age_max} ages"
    age_range.short_description = "Age range"

    def price_range(self, obj):
        if obj.min_price and obj.max_price:
            return f"{obj.min_price}-{obj.max_price}"
        return "Not available"
    price_range.short_description = "Price range"

@admin.register(GiftImage)
class GiftImageAdmin(admin.ModelAdmin):
    list_display = ('gift', 'image_preview', 'alt_text', 'order')
    list_filter = ('gift',)
    search_fields = ('gift__name', 'alt_text')
    ordering = ['gift', 'order']
    list_editable = ('order',)

    def image_preview(self, obj):
        if obj.image:
            return mark_safe(f'<img src="{obj.image.url}"></img>')
        return "No image"
    image_preview.short_description = "Preview"
