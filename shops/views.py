from datetime import timedelta

from django.db.models import Sum, F
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import ProductLinkFilter
from .models import Shop, ProductLink, PriceHistory, ShopClick
from .serializers import (
    ShopSerializer,
    ProductLinkSerializer,
    PriceHistorySerializer,
)

class ShopListView(generics.ListAPIView):
    """GET /api/shops/ — list of active shops sorted by priority DESC."""
    serializer_class = ShopSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = Shop.objects.filter(is_active=True).order_by('-priority')
        shop_type = self.request.query_params.get('shop_type')
        if shop_type:
            qs = qs.filter(shop_type=shop_type)
        return qs


class ShopDetailView(generics.RetrieveAPIView):
    """GET /api/shops/{slug}/ — shop details + statistics."""
    serializer_class = ShopSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'

    def get_queryset(self):
        return Shop.objects.filter(is_active=True)

class ProductLinkListView(generics.ListAPIView):
    """
    GET /api/shops/products/
    Filters: ?gift_id= &shop_id= &in_stock= &price_min= &price_max=
    Ordering: ?ordering=price,-click_count
    """
    serializer_class = ProductLinkSerializer
    permission_classes = [AllowAny]
    filterset_class = ProductLinkFilter
    ordering_fields = ['price', 'click_count', 'last_price_update']
    ordering = ['price']

    def get_queryset(self):
        return ProductLink.objects.select_related('shop').all()


class ProductLinkDetailView(generics.RetrieveAPIView):
    """GET /api/shops/products/{id}/ — detail + current price + shop."""
    serializer_class = ProductLinkSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return ProductLink.objects.select_related('shop').all()


class ProductLinkClickView(APIView):
    """
    POST /api/shops/products/{id}/click/
    Records a ShopClick, atomically increments click_count,
    returns the affiliate_url (no redirect).
    """
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            link = ProductLink.objects.select_related('shop').get(pk=pk)
        except ProductLink.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Record the click
        ShopClick.objects.create(
            product_link=link,
            user=request.user if request.user.is_authenticated else None,
            session_key=request.data.get('session_key', request.session.session_key or ''),
            ip_address=self._get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
            referrer=request.data.get('referrer'),
        )

        # Atomic increment
        from .tasks import increment_shop_click
        increment_shop_click.delay(link.id)

        # Build affiliate URL if applicable
        affiliate_url = link.product_url
        if link.shop.has_affiliate and link.shop.affiliate_parameter:
            from .scrapers.base import BaseShopScraper
            affiliate_url = BaseShopScraper().build_affiliate_url(
                link.product_url, link.shop.affiliate_parameter,
            )

        return Response({'affiliate_url': affiliate_url}, status=status.HTTP_200_OK)

    @staticmethod
    def _get_client_ip(request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '127.0.0.1')


class PriceHistoryView(generics.ListAPIView):
    """
    GET /api/shops/products/{id}/price-history/
    Returns price history entries for the last N days (?days=30).
    """
    serializer_class = PriceHistorySerializer
    permission_classes = [AllowAny]
    pagination_class = None  # return full list

    def get_queryset(self):
        product_link_id = self.kwargs['pk']
        days = int(self.request.query_params.get('days', 30))
        cutoff = timezone.now() - timedelta(days=days)
        return PriceHistory.objects.filter(
            product_link_id=product_link_id,
            recorded_at__gte=cutoff,
        ).order_by('-recorded_at')

class AnalyticsView(APIView):
    """
    GET /api/shops/analytics/
    Top-10 shops by clicks, top-10 product links by clicks,
    click counts for last 7 / 30 days.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        now = timezone.now()

        top_shops = list(
            Shop.objects.order_by('-click_count').values('id', 'name', 'slug', 'click_count')[:10]
        )
        top_links = list(
            ProductLink.objects.select_related('shop')
            .order_by('-click_count')
            .values('id', 'product_name', 'shop__name', 'click_count')[:10]
        )

        clicks_7d = ShopClick.objects.filter(
            clicked_at__gte=now - timedelta(days=7),
        ).count()
        clicks_30d = ShopClick.objects.filter(
            clicked_at__gte=now - timedelta(days=30),
        ).count()

        return Response({
            'top_shops': top_shops,
            'top_product_links': top_links,
            'clicks_last_7_days': clicks_7d,
            'clicks_last_30_days': clicks_30d,
        })


class ScraperStatusView(APIView):
    """
    GET /api/shops/scraper-status/
    Status of the last scraper run for each active shop.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        shops = Shop.objects.filter(is_active=True)
        result = []
        for shop in shops:
            latest_link = (
                ProductLink.objects.filter(shop=shop)
                .order_by('-updated_at')
                .first()
            )
            result.append({
                'shop_slug': shop.slug,
                'last_run': latest_link.updated_at if latest_link else None,
                'products_found': shop.total_products,
                'errors_count': 0,  # Could be extended with error tracking
            })
        return Response(result)

class TriggerDiscoveryView(APIView):
    """
    POST /api/shops/trigger-discovery/{shop_slug}/
    Manually trigger scraper discovery for a specific shop (staff only).
    Returns the Celery task IDs.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, shop_slug):
        try:
            shop = Shop.objects.get(slug=shop_slug, is_active=True)
        except Shop.DoesNotExist:
            return Response(
                {'detail': f'Shop "{shop_slug}" not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .scrapers import get_scraper
        scraper = get_scraper(shop.slug)
        if scraper is None:
            return Response(
                {'detail': f'No scraper for shop "{shop_slug}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import discover_shop_products
        task_ids = []
        for url in scraper.get_category_urls():
            result = discover_shop_products.delay(shop.id, url)
            task_ids.append(result.id)

        return Response({
            'shop': shop.slug,
            'task_ids': task_ids,
            'categories_queued': len(task_ids),
        }, status=status.HTTP_202_ACCEPTED)