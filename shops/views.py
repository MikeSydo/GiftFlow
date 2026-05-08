from datetime import timedelta

from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .connectors.base import BaseConnector
from .filters import ProductLinkFilter
from .models import PriceHistory, ProductLink, Shop, ShopClick, ShopSource
from .serializers import PriceHistorySerializer, ProductLinkSerializer, ShopSerializer


def annotate_best_offer(queryset):
    cheaper_offer_exists = ProductLink.objects.filter(
        gift_id=OuterRef("gift_id"),
        in_stock=True,
    ).exclude(
        id=OuterRef("id"),
    ).filter(
        price__lt=OuterRef("price"),
    )

    same_price_lower_id_exists = ProductLink.objects.filter(
        gift_id=OuterRef("gift_id"),
        in_stock=True,
        price=OuterRef("price"),
        id__lt=OuterRef("id"),
    )

    queryset = queryset.annotate(
        has_cheaper_offer=Exists(cheaper_offer_exists),
        has_same_price_lower_offer=Exists(same_price_lower_id_exists),
    )
    return queryset.annotate(
        is_best_offer_annotated=Case(
            When(
                in_stock=True,
                has_cheaper_offer=False,
                has_same_price_lower_offer=False,
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


class ShopListView(generics.ListAPIView):
    serializer_class = ShopSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Shop.objects.filter(is_active=True).order_by("-priority")
        shop_type = self.request.query_params.get("shop_type")
        if shop_type:
            queryset = queryset.filter(shop_type=shop_type)
        return queryset


class ShopDetailView(generics.RetrieveAPIView):
    serializer_class = ShopSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return Shop.objects.filter(is_active=True)


class ProductLinkListView(generics.ListAPIView):
    serializer_class = ProductLinkSerializer
    permission_classes = [AllowAny]
    filterset_class = ProductLinkFilter
    ordering_fields = ["price", "click_count", "last_price_update"]
    ordering = ["price", "id"]

    def get_queryset(self):
        queryset = ProductLink.objects.select_related("shop", "gift")
        return annotate_best_offer(queryset)


class ProductLinkDetailView(generics.RetrieveAPIView):
    serializer_class = ProductLinkSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = ProductLink.objects.select_related("shop", "gift")
        return annotate_best_offer(queryset)


class ProductLinkClickView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            link = ProductLink.objects.select_related("shop").get(pk=pk)
        except ProductLink.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        ShopClick.objects.create(
            product_link=link,
            user=request.user if request.user.is_authenticated else None,
            session_key=request.data.get("session_key", request.session.session_key or ""),
            ip_address=self._get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
            referrer=request.data.get("referrer"),
        )

        from .tasks import increment_shop_click

        increment_shop_click.delay(link.id)

        affiliate_url = link.product_url
        if link.shop.has_affiliate and link.shop.affiliate_parameter:
            affiliate_url = BaseConnector.build_affiliate_url(
                link.product_url, link.shop.affiliate_parameter,
            )

        return Response({"affiliate_url": affiliate_url}, status=status.HTTP_200_OK)

    @staticmethod
    def _get_client_ip(request):
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            return x_forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "127.0.0.1")


class PriceHistoryView(generics.ListAPIView):
    serializer_class = PriceHistorySerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        product_link_id = self.kwargs["pk"]
        days = int(self.request.query_params.get("days", 30))
        cutoff = timezone.now() - timedelta(days=days)
        return PriceHistory.objects.filter(
            product_link_id=product_link_id,
            recorded_at__gte=cutoff,
        ).order_by("-recorded_at")


class AnalyticsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        now = timezone.now()
        top_shops = list(
            Shop.objects.order_by("-click_count").values("id", "name", "slug", "click_count")[:10]
        )
        top_links = list(
            ProductLink.objects.select_related("shop")
            .order_by("-click_count")
            .values("id", "product_name", "shop__name", "seller_name", "click_count")[:10]
        )
        clicks_7d = ShopClick.objects.filter(clicked_at__gte=now - timedelta(days=7)).count()
        clicks_30d = ShopClick.objects.filter(clicked_at__gte=now - timedelta(days=30)).count()
        return Response({
            "top_shops": top_shops,
            "top_product_links": top_links,
            "clicks_last_7_days": clicks_7d,
            "clicks_last_30_days": clicks_30d,
        })


class ScraperStatusView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        shops = Shop.objects.filter(is_active=True)
        result = []
        for shop in shops:
            latest_link = ProductLink.objects.filter(shop=shop).order_by("-updated_at").first()
            result.append({
                "shop_slug": shop.slug,
                "last_run": latest_link.updated_at if latest_link else None,
                "products_found": shop.total_products,
                "errors_count": 0,
            })
        return Response(result)


class TriggerDiscoveryView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, shop_slug):
        try:
            shop = Shop.objects.get(slug=shop_slug, is_active=True)
        except Shop.DoesNotExist:
            return Response(
                {"detail": f'Shop "{shop_slug}" not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        sources = list(
            ShopSource.objects.filter(
                integration__shop=shop,
                integration__is_active=True,
                is_active=True,
            ).values_list("id", flat=True)
        )
        if not sources:
            return Response(
                {"detail": f'No active sources for shop "{shop_slug}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import discover_source_products

        task_ids = []
        for source_id in sources:
            result = discover_source_products.delay(source_id)
            task_ids.append(result.id)

        return Response({
            "shop": shop.slug,
            "task_ids": task_ids,
            "sources_queued": len(task_ids),
        }, status=status.HTTP_202_ACCEPTED)
