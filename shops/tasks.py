import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import F, Max, Min, Q
from django.utils import timezone

logger = logging.getLogger("shops.tasks")


@shared_task(queue="prices")
def update_gift_price_cache(gift_id: int):
    from gifts.models import Gift

    from .models import ProductLink

    result = ProductLink.objects.filter(
        gift_id=gift_id,
        in_stock=True,
    ).aggregate(min_p=Min("price"), max_p=Max("price"))

    Gift.objects.filter(id=gift_id).update(
        min_price=result["min_p"],
        max_price=result["max_p"],
    )
    logger.info(
        "[cache] updated Gift id=%d price cache min=%s max=%s",
        gift_id, result["min_p"], result["max_p"],
    )


@shared_task(queue="verification")
def verify_product_link(product_link_id: int):
    import httpx

    from .models import ProductLink

    link = ProductLink.objects.get(id=product_link_id)
    now = timezone.now()
    try:
        resp = httpx.head(link.product_url, timeout=15, follow_redirects=True)
        link.is_verified = resp.status_code == 200
    except httpx.RequestError:
        link.is_verified = False

    link.last_checked = now
    link.save(update_fields=["is_verified", "last_checked"])
    logger.info("[verify] ProductLink id=%d verified=%s", link.id, link.is_verified)


@shared_task(queue="prices")
def increment_shop_click(product_link_id: int):
    from .models import ProductLink, Shop

    ProductLink.objects.filter(id=product_link_id).update(
        click_count=F("click_count") + 1,
    )
    link = ProductLink.objects.select_related("shop").get(id=product_link_id)
    Shop.objects.filter(id=link.shop_id).update(
        click_count=F("click_count") + 1,
    )


@shared_task
def trigger_all_verifications():
    from .models import ProductLink

    cutoff = timezone.now() - timedelta(hours=24)
    links = ProductLink.objects.filter(
        Q(last_checked__isnull=True) | Q(last_checked__lt=cutoff),
    ).values_list("id", flat=True)[:500]

    for link_id in links:
        verify_product_link.delay(link_id)
    logger.info("[beat] queued verification for %d links", len(links))
