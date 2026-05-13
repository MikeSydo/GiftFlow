import logging
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db.models import F, Max, Min, Q
from django.utils import timezone

from .discovery import QueryBuilder, request_as_namespace
from .models import normalize_product_url

logger = logging.getLogger("shops.tasks")


def _match_or_create_gift(product_data: dict, shop):
    from django.utils.text import slugify
    from rapidfuzz import fuzz

    from gifts.models import Gift

    name = product_data["name"]
    price = Decimal(str(product_data["price"]))

    best_gift = None
    best_score = 0
    for gift in Gift.objects.filter(is_active=True).only("id", "name"):
        score = fuzz.token_sort_ratio(name, gift.name)
        if score > best_score:
            best_score = score
            best_gift = gift

    if best_score >= 85 and best_gift is not None:
        return best_gift

    slug = slugify(name)[:100] or f"auto-{shop.slug}-{timezone.now().timestamp()}"
    base_slug = slug
    counter = 1
    while Gift.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    gift = Gift.objects.create(
        name=name[:100],
        slug=slug,
        is_active=False,
        min_price=price,
        max_price=price,
        image_url=product_data.get("image_url")[:1000] if product_data.get("image_url") else None,
    )
    logger.info("[%s] created new inactive Gift id=%d name=%s", shop.slug, gift.id, name)
    return gift


def _resolve_offer_lookup(shop, product_data: dict) -> tuple[dict, str, str | None]:
    normalized_url = normalize_product_url(product_data["url"])
    external_offer_id = (product_data.get("external_offer_id") or "")[:200] or None

    if external_offer_id:
        return {"shop": shop, "external_offer_id": external_offer_id}, normalized_url, external_offer_id

    if normalized_url:
        return {"shop": shop, "normalized_product_url": normalized_url}, normalized_url, None

    return {"shop": shop, "product_url": product_data["url"]}, normalized_url, None


def _get_integration_for_link(link):
    if link.discovered_via_source_id:
        return link.discovered_via_source.integration

    from .models import ShopIntegration

    return (
        ShopIntegration.objects.filter(shop=link.shop, is_active=True)
        .order_by("-priority", "id")
        .first()
    )


@shared_task(queue="discovery", rate_limit="10/m")
def discover_source_products(source_id: int):
    from .connectors import get_connector
    from .models import ShopSource

    source = (
        ShopSource.objects.select_related("integration", "integration__shop")
        .get(id=source_id, is_active=True, integration__is_active=True)
    )
    connector = get_connector(source.integration)
    requests = QueryBuilder().build(source)

    try:
        with connector:
            discovered_count = 0
            request_count = len(requests)
            for request in requests:
                products = connector.discover_products(request_as_namespace(request))
                discovered_count += len(products)
                logger.info(
                    "[%s] discovered %d products from source=%d request=%s",
                    source.integration.shop.slug, len(products), source.id, request.value,
                )
                for product in products:
                    process_discovered_product.delay(asdict(product), source.id)

        logger.info(
            "[%s] discovered %d products from source=%d across %d request(s)",
            source.integration.shop.slug, discovered_count, source.id, request_count,
        )
    except Exception as exc:
        logger.exception(
            "[%s] discovery failed for source=%d: %s",
            source.integration.shop.slug, source.id, exc,
        )


@shared_task(queue="discovery", rate_limit="10/m")
def discover_shop_products(shop_id: int, category_url: str):
    from .models import ShopSource

    source = (
        ShopSource.objects.filter(
            integration__shop_id=shop_id,
            is_active=True,
            integration__is_active=True,
            value=category_url,
        )
        .order_by("-priority", "id")
        .first()
    )
    if source is None:
        logger.error("No source registered for shop_id=%s url=%s", shop_id, category_url)
        return
    discover_source_products.delay(source.id)


@shared_task(queue="discovery")
def process_discovered_product(product_data: dict, source_id: int):
    from .models import PriceHistory, ProductLink, Shop, ShopSource
    from .services import CategoryMatcher

    source = (
        ShopSource.objects.select_related("integration", "integration__shop")
        .get(id=source_id)
    )
    shop = source.integration.shop
    gift = _match_or_create_gift(product_data, shop)

    raw_category = (product_data.get("category_hint") or "").strip()
    matcher = CategoryMatcher()
    match_result = matcher.match(shop=shop, raw=raw_category)

    if match_result.category and not match_result.needs_review and not gift.category_id:
        gift.category = match_result.category
        gift.save(update_fields=["category"])
        logger.info(
            "[%s] auto-assigned category '%s' (score=%.1f) to Gift id=%d",
            shop.slug, match_result.category.name, match_result.confidence, gift.id,
        )

    price = Decimal(str(product_data["price"]))
    original_price = (
        Decimal(str(product_data["original_price"]))
        if product_data.get("original_price") else None
    )

    lookup, normalized_url, external_offer_id = _resolve_offer_lookup(shop, product_data)
    now = timezone.now()
    link, created = ProductLink.objects.update_or_create(
        **lookup,
        defaults={
            "gift": gift,
            "shop": shop,
            "discovered_via_source": source,
            "product_url": product_data["url"],
            "normalized_product_url": normalized_url,
            "product_name": product_data["name"][:300],
            "price": price,
            "original_price": original_price,
            "in_stock": product_data.get("in_stock", True),
            "sku": (product_data.get("sku") or "")[:100] or None,
            "image_url": product_data.get("image_url")[:1000] if product_data.get("image_url") else None,
            "last_price_update": now,
            "external_offer_id": external_offer_id,
            "external_product_id": (product_data.get("external_product_id") or "")[:200] or None,
            "seller_name": (product_data.get("seller_name") or "")[:200] or None,
            "seller_external_id": (product_data.get("seller_external_id") or "")[:200] or None,
            "seller_url": product_data.get("seller_url"),
            "is_marketplace_offer": product_data.get("is_marketplace_offer", False),
            "original_category_name": raw_category[:300],
            "category_confidence": match_result.confidence,
            "needs_category_review": match_result.needs_review,
        },
    )

    PriceHistory.objects.create(
        product_link=link,
        price=price,
        in_stock=product_data.get("in_stock", True),
    )

    update_gift_price_cache.delay(gift.id)
    Shop.objects.filter(id=shop.id).update(
        total_products=ProductLink.objects.filter(shop=shop).count()
    )
    logger.info(
        "[%s] %s ProductLink id=%d for Gift id=%d",
        shop.slug, "created" if created else "updated", link.id, gift.id,
    )


@shared_task(queue="prices", rate_limit="30/m")
def update_product_price(product_link_id: int):
    from .connectors import get_connector
    from .models import PriceHistory, ProductLink

    link = (
        ProductLink.objects.select_related(
            "shop",
            "discovered_via_source",
            "discovered_via_source__integration",
        )
        .get(id=product_link_id)
    )
    integration = _get_integration_for_link(link)
    if integration is None:
        logger.error("No active integration for ProductLink id=%d", product_link_id)
        return

    connector = get_connector(integration)

    try:
        with connector:
            data = connector.fetch_product_detail(link)

        now = timezone.now()
        price_changed = data.price != link.price
        stock_changed = data.in_stock != link.in_stock

        if price_changed or stock_changed:
            PriceHistory.objects.create(
                product_link=link,
                price=data.price,
                in_stock=data.in_stock,
            )

        link.price = data.price
        link.in_stock = data.in_stock
        link.last_price_update = now
        link.last_checked = now
        link.original_price = data.original_price
        link.image_url = data.image_url
        link.seller_name = data.seller_name or link.seller_name
        link.seller_external_id = data.seller_external_id or link.seller_external_id
        link.seller_url = data.seller_url or link.seller_url
        if data.sku:
            link.sku = data.sku
        link.save(update_fields=[
            "price", "in_stock", "last_price_update", "last_checked",
            "original_price", "image_url", "seller_name",
            "seller_external_id", "seller_url", "sku",
        ])

        update_gift_price_cache.delay(link.gift_id)
        logger.info("[price] updated ProductLink id=%d price=%s", link.id, data.price)
    except Exception as exc:
        logger.exception("[price] failed for ProductLink id=%d: %s", product_link_id, exc)


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
def trigger_all_shop_discovery():
    from .models import ShopSource

    source_ids = ShopSource.objects.filter(
        is_active=True,
        integration__is_active=True,
        integration__shop__is_active=True,
    ).values_list("id", flat=True)

    for source_id in source_ids:
        discover_source_products.delay(source_id)
    logger.info("[beat] triggered discovery for %d sources", len(source_ids))


@shared_task
def trigger_all_price_updates():
    from .models import ProductLink

    links = ProductLink.objects.filter(
        shop__is_active=True,
    ).order_by("last_price_update").values_list("id", flat=True)[:500]

    for link_id in links:
        update_product_price.delay(link_id)
    logger.info("[beat] queued price update for %d links", len(links))


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
