from rest_framework import serializers

from .models import (
    PriceHistory,
    ProductLink,
    Shop,
    ShopClick,
    ShopCategoryAlias,
    ShopIntegration,
    ShopSource,
)


class ShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id", "name", "slug", "website", "shop_type",
            "logo", "primary_color", "priority",
            "total_products", "click_count", "conversion_rate",
        ]


class ProductLinkSerializer(serializers.ModelSerializer):
    shop = ShopSerializer(read_only=True)
    discount_percent = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    seller_name = serializers.CharField(read_only=True)
    is_best_offer = serializers.SerializerMethodField()

    class Meta:
        model = ProductLink
        fields = [
            "id", "gift", "shop", "seller_name", "product_url", "product_name",
            "price", "original_price", "currency", "discount_percent",
            "in_stock", "stock_quantity", "is_verified", "is_best_offer",
            "image_url", "external_offer_id", "external_product_id",
            "is_marketplace_offer", "last_price_update", "click_count",
        ]

    def get_discount_percent(self, obj) -> int | None:
        if obj.original_price and obj.original_price > obj.price:
            return round((1 - obj.price / obj.original_price) * 100)
        return None

    def get_currency(self, obj) -> str:
        return "UAH"

    def get_is_best_offer(self, obj) -> bool:
        annotated = getattr(obj, "is_best_offer_annotated", None)
        if annotated is not None:
            return bool(annotated)
        return False


class PriceHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceHistory
        fields = ["price", "in_stock", "recorded_at"]


class ShopClickCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopClick
        fields = ["product_link", "session_key", "referrer"]
    # ip_address and user_agent are set automatically in the view


class ShopIntegrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopIntegration
        fields = [
            "id", "shop", "connector_type", "base_url", "auth_type",
            "auth_config", "request_config", "field_mapping",
            "is_active", "priority",
        ]


class ShopSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopSource
        fields = [
            "id", "integration", "source_type", "value", "config",
            "is_active", "priority",
        ]


class ShopCategoryAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopCategoryAlias
        fields = [
            "id", "shop", "raw_category", "normalized_category",
            "category", "confidence", "status",
        ]
