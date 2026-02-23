from rest_framework import serializers

from .models import Shop, ProductLink, PriceHistory, ShopClick


class ShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            'id', 'name', 'slug', 'website', 'shop_type',
            'logo', 'primary_color', 'priority',
            'total_products', 'click_count', 'conversion_rate',
        ]


class ProductLinkSerializer(serializers.ModelSerializer):
    shop = ShopSerializer(read_only=True)
    discount_percent = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta:
        model = ProductLink
        fields = [
            'id', 'shop', 'product_url', 'product_name',
            'price', 'original_price', 'currency', 'discount_percent',
            'in_stock', 'stock_quantity', 'is_verified',
            'last_price_update', 'click_count',
        ]

    def get_discount_percent(self, obj) -> int | None:
        if obj.original_price and obj.original_price > obj.price:
            return round((1 - obj.price / obj.original_price) * 100)
        return None

    def get_currency(self, obj) -> str:
        return 'UAH'


class PriceHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceHistory
        fields = ['price', 'in_stock', 'recorded_at']


class ShopClickCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopClick
        fields = ['product_link', 'session_key', 'referrer']
    # ip_address and user_agent are set automatically in the view
