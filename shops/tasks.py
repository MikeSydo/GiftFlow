import logging
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db.models import F, Min, Max
from django.utils import timezone

logger = logging.getLogger('shops.tasks')


@shared_task(queue='discovery', rate_limit='10/m')
def discover_shop_products(shop_id: int, category_url: str):
    """
    Download a catalogue page for a shop and parse products.
    For each product found, dispatch process_discovered_product.
    """
    from .models import Shop
    from .scrapers import get_scraper

    shop = Shop.objects.get(id=shop_id)
    scraper = get_scraper(shop.slug)
    if scraper is None:
        logger.error('No scraper registered for shop slug=%s', shop.slug)
        return

    try:
        with scraper:
            # Some scrapers (e.g. Rozetka) need to extract data while
            # the browser is still open (Shadow DOM / Angular).
            if hasattr(scraper, 'fetch_and_parse'):
                products = scraper.fetch_and_parse(category_url)
            else:
                html = scraper.fetch(category_url)
                products = scraper.parse_product_list(html, category_url)

            logger.info(
                '[%s] discovered %d products from %s',
                shop.slug, len(products), category_url,
            )
            for product in products:
                process_discovered_product.delay(asdict(product), shop_id)
    except Exception as exc:
        logger.exception('[%s] discovery failed for %s: %s', shop.slug, category_url, exc)


@shared_task(queue='discovery')
def process_discovered_product(product_data: dict, shop_id: int):
    """
    Match a scraped product to an existing Gift (fuzzy), or create a new
    inactive Gift.  Then create/update the ProductLink + PriceHistory.
    """
    from rapidfuzz import fuzz
    from gifts.models import Gift
    from .models import Shop, ProductLink, PriceHistory
    from .services import CategoryMatcher

    shop = Shop.objects.get(id=shop_id)
    name = product_data['name']
    price = Decimal(str(product_data['price']))
    original_price = (
        Decimal(str(product_data['original_price']))
        if product_data.get('original_price') else None
    )
    url = product_data['url']

    best_gift = None
    best_score = 0
    for gift in Gift.objects.filter(is_active=True).only('id', 'name'):
        score = fuzz.token_sort_ratio(name, gift.name)
        if score > best_score:
            best_score = score
            best_gift = gift

    if best_score < 85 or best_gift is None:
        # Create a new inactive gift for admin review
        from django.utils.text import slugify
        slug = slugify(name)[:100] or f'auto-{shop.slug}-{timezone.now().timestamp()}'
        # ensure uniqueness
        base_slug = slug
        counter = 1
        while Gift.objects.filter(slug=slug).exists():
            slug = f'{base_slug}-{counter}'
            counter += 1
        best_gift = Gift.objects.create(
            name=name[:100],
            slug=slug,
            is_active=False,
            min_price=price,
            max_price=price,
        )
        logger.info('[%s] created new inactive Gift id=%d name=%s', shop.slug, best_gift.id, name)

    # Run category matcher against the hint provided by the scraper
    raw_category = (product_data.get('category_hint') or '').strip()
    matcher = CategoryMatcher()
    match_result = matcher.match(raw_category)

    # Auto-assign category to Gift when confidence is high and Gift has none yet
    if match_result.category and not match_result.needs_review and not best_gift.category_id:
        best_gift.category = match_result.category
        best_gift.save(update_fields=['category'])
        logger.info(
            '[%s] auto-assigned category "%s" (score=%.1f) to Gift id=%d',
            shop.slug, match_result.category.name, match_result.confidence, best_gift.id,
        )

    # create / update ProductLink
    now = timezone.now()
    link, created = ProductLink.objects.update_or_create(
        gift=best_gift,
        shop=shop,
        defaults={
            'product_url': url,
            'product_name': name[:300],
            'price': price,
            'original_price': original_price,
            'in_stock': product_data.get('in_stock', True),
            'sku': (product_data.get('sku') or '')[:100] or None,
            'last_price_update': now,
            # Category matching results
            'original_category_name': raw_category[:300],
            'category_confidence': match_result.confidence,
            'needs_category_review': match_result.needs_review,
        },
    )

    # PriceHistory
    PriceHistory.objects.create(
        product_link=link,
        price=price,
        in_stock=product_data.get('in_stock', True),
    )

    # update gift price cache
    update_gift_price_cache.delay(best_gift.id)

    # Update shop total_products count
    Shop.objects.filter(id=shop_id).update(
        total_products=ProductLink.objects.filter(shop_id=shop_id).count()
    )

    logger.info(
        '[%s] %s ProductLink id=%d for Gift id=%d',
        shop.slug, 'created' if created else 'updated', link.id, best_gift.id,
    )

@shared_task(queue='prices', rate_limit='30/m')
def update_product_price(product_link_id: int):
    """Re-scrape a single product page and update price / stock."""
    from .models import ProductLink, PriceHistory
    from .scrapers import get_scraper

    link = ProductLink.objects.select_related('shop').get(id=product_link_id)
    scraper = get_scraper(link.shop.slug)
    if scraper is None:
        logger.error('No scraper for shop slug=%s', link.shop.slug)
        return

    try:
        with scraper:
            html = scraper.fetch(link.product_url)
            data = scraper.parse_product_detail(html, link.product_url)

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
        if data.original_price:
            link.original_price = data.original_price
        link.save(update_fields=[
            'price', 'in_stock', 'last_price_update',
            'last_checked', 'original_price',
        ])

        update_gift_price_cache.delay(link.gift_id)
        logger.info('[price] updated ProductLink id=%d price=%s', link.id, data.price)

    except Exception as exc:
        logger.exception('[price] failed for ProductLink id=%d: %s', product_link_id, exc)


@shared_task(queue='prices')
def update_gift_price_cache(gift_id: int):
    """Atomically recalculate min/max price on Gift from active in-stock ProductLinks."""
    from gifts.models import Gift
    from .models import ProductLink

    result = ProductLink.objects.filter(
        gift_id=gift_id, in_stock=True,
    ).aggregate(min_p=Min('price'), max_p=Max('price'))

    Gift.objects.filter(id=gift_id).update(
        min_price=result['min_p'],
        max_price=result['max_p'],
    )
    logger.info('[cache] updated Gift id=%d price cache min=%s max=%s',
                gift_id, result['min_p'], result['max_p'])


@shared_task(queue='verification')
def verify_product_link(product_link_id: int):
    """Check that the product URL is still reachable (HTTP 200)."""
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
    link.save(update_fields=['is_verified', 'last_checked'])
    logger.info('[verify] ProductLink id=%d verified=%s', link.id, link.is_verified)


@shared_task(queue='prices')
def increment_shop_click(product_link_id: int):
    """Atomic increment of click_count on ProductLink and Shop."""
    from .models import ProductLink

    ProductLink.objects.filter(id=product_link_id).update(
        click_count=F('click_count') + 1,
    )
    link = ProductLink.objects.select_related('shop').get(id=product_link_id)
    from .models import Shop
    Shop.objects.filter(id=link.shop_id).update(
        click_count=F('click_count') + 1,
    )


@shared_task
def trigger_all_shop_discovery():
    """Queue discovery for every active shop's category URLs."""
    from .models import Shop
    from .scrapers import get_scraper

    for shop in Shop.objects.filter(is_active=True):
        scraper = get_scraper(shop.slug)
        if scraper is None:
            continue
        for url in scraper.get_category_urls():
            discover_shop_products.delay(shop.id, url)
    logger.info('[beat] triggered discovery for all active shops')


@shared_task
def trigger_all_price_updates():
    """Queue price update for every active ProductLink, oldest-checked first."""
    from .models import ProductLink

    links = ProductLink.objects.filter(
        shop__is_active=True,
    ).order_by('last_price_update').values_list('id', flat=True)[:500]

    for link_id in links:
        update_product_price.delay(link_id)
    logger.info('[beat] queued price update for %d links', len(links))


@shared_task
def trigger_all_verifications():
    """Queue verification for all ProductLinks not checked in 24 h."""
    from django.db.models import Q
    from .models import ProductLink

    cutoff = timezone.now() - timedelta(hours=24)
    links = ProductLink.objects.filter(
        Q(last_checked__isnull=True) | Q(last_checked__lt=cutoff),
    ).values_list('id', flat=True)[:500]

    for link_id in links:
        verify_product_link.delay(link_id)
    logger.info('[beat] queued verification for %d links', len(links))
