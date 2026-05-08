import django_filters

from .models import ProductLink


class ProductLinkFilter(django_filters.FilterSet):
    gift_id = django_filters.NumberFilter(field_name="gift_id")
    shop_id = django_filters.NumberFilter(field_name="shop_id")
    in_stock = django_filters.BooleanFilter(field_name="in_stock")
    price_min = django_filters.NumberFilter(field_name="price", lookup_expr="gte")
    price_max = django_filters.NumberFilter(field_name="price", lookup_expr="lte")
    seller_name = django_filters.CharFilter(field_name="seller_name", lookup_expr="icontains")
    marketplace_only = django_filters.BooleanFilter(field_name="is_marketplace_offer")

    class Meta:
        model = ProductLink
        fields = [
            "gift_id",
            "shop_id",
            "in_stock",
            "price_min",
            "price_max",
            "seller_name",
            "marketplace_only",
        ]
