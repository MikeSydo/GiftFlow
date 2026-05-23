from __future__ import annotations

import logging
import mimetypes
import re
from datetime import timedelta
from decimal import Decimal
from html import unescape
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.db.models import Max, Min, OuterRef, Q, QuerySet, Subquery
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from gifts.default_tags import seed_default_tags
from gifts.models import Category, Gift, Tag
from shops.models import PriceHistory, ProductLink, Shop, normalize_product_url
from shops.signals import suppress_productlink_price_cache_updates

from .models import HotlineSeed, IngestionRun

logger = logging.getLogger("search.services")

MAX_RESULTS = 20
HOTLINE_CATALOG_SOURCE = Gift.CATALOG_SOURCE_HOTLINE
HOTLINE_IMAGE_TIMEOUT = 15
HOTLINE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
HOTLINE_PLACEHOLDER_MAX_BYTES = 4096
HOTLINE_IMAGE_ALLOWED_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}
HOTLINE_OG_IMAGE_PATTERN = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
HOTLINE_TX_PLACEHOLDER_IMAGE_PATTERN = re.compile(
    r"^(?P<prefix>https://hotline\.ua/img/tx/\d+/\d+)0(?P<suffix>\.[a-z0-9]+)$",
    re.I,
)
HOTLINE_TAG_RULES = {
    "electronics": [("Tech", "I")],
    "gaming": [("Gaming", "I"), ("Teen", "A"), ("Adult", "A")],
    "home-kitchen": [("Home", "I")],
    "beauty": [("Beauty", "I")],
    "fashion": [("Adult", "A")],
    "accessories": [("Adult", "A")],
    "sports-outdoors": [("Fitness", "I")],
    "auto": [("Auto", "I"), ("Adult", "A")],
    "toys-kids": [("Toys", "I"), ("Kids", "A"), ("Child", "R")],
    "hobbies-creativity": [("Creativity", "I")],
    "pets": [("Pets", "I")],
    "office": [("Colleague", "R"), ("Adult", "A")],
    "travel": [("Travel", "I")],
}
HOTLINE_KEYWORD_TAG_RULES = (
    (("coffee", "espresso", "cappuccino"), ("Coffee", "I")),
    (("playstation", "xbox", "nintendo", "gamepad", "gaming", "headset"), ("Gaming", "I")),
    (("fitness", "dumbbell", "scooter"), ("Fitness", "I")),
    (("pet", "cat", "dog"), ("Pets", "I")),
    (("backpack", "suitcase", "thermos"), ("Travel", "I")),
    (("hair", "shaver", "beauty"), ("Beauty", "I")),
)


def normalize_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def extract_request_filters(data) -> dict:
    filters: dict[str, str] = {}
    for key in ("category", "gender", "age", "budget_min", "budget_max", "tags"):
        value = data.get(key)
        if value not in (None, ""):
            filters[key] = str(value)
    return filters


def refresh_gift_price_cache(gift_id: int) -> None:
    result = ProductLink.objects.filter(
        gift_id=gift_id,
        in_stock=True,
    ).aggregate(min_price=Min("price"), max_price=Max("price"))

    Gift.objects.filter(id=gift_id).update(
        min_price=result["min_price"],
        max_price=result["max_price"],
    )


def ensure_hotline_category(seed: HotlineSeed) -> Category:
    category = getattr(seed, "category", None)
    if category is not None:
        return category

    category, _ = Category.objects.get_or_create(
        slug=seed.category_slug,
        defaults={
            "name": seed.category_name,
            "description": f"Hotline seed category: {seed.category_name}",
            "is_active": True,
        },
    )
    return category


def assign_hotline_gift_tags(gift: Gift, seed: HotlineSeed, title: str) -> bool:
    seed_default_tags()
    category = ensure_hotline_category(seed)
    wanted: set[tuple[str, str]] = set(HOTLINE_TAG_RULES.get(category.slug, []))
    text = normalize_search_query(f"{seed.query} {title}")
    for keywords, tag in HOTLINE_KEYWORD_TAG_RULES:
        if any(keyword in text for keyword in keywords):
            wanted.add(tag)

    if not wanted:
        return False

    tags = list(
        Tag.objects.filter(
            name__in=[name for name, _ in wanted],
            tag_type__in=[tag_type for _, tag_type in wanted],
        )
    )
    current_ids = set(gift.tags.values_list("id", flat=True))
    new_tags = [tag for tag in tags if tag.id not in current_ids]
    if not new_tags:
        return False

    gift.tags.add(*new_tags)
    return True


def build_unique_gift_name(title: str, *, source_product_id: str | None = None, exclude_id: int | None = None) -> str:
    base_name = re.sub(r"\s+", " ", (title or "").strip())[:100] or "Untitled gift"
    existing = Gift.objects.filter(name__iexact=base_name)
    if exclude_id is not None:
        existing = existing.exclude(id=exclude_id)
    if not existing.exists():
        return base_name

    if source_product_id:
        suffix = f" ({source_product_id})"
        trimmed = base_name[: max(1, 100 - len(suffix))]
        candidate = f"{trimmed}{suffix}"
        existing = Gift.objects.filter(name__iexact=candidate)
        if exclude_id is not None:
            existing = existing.exclude(id=exclude_id)
        if not existing.exists():
            return candidate

    counter = 2
    while True:
        suffix = f" ({counter})"
        candidate = f"{base_name[: max(1, 100 - len(suffix))]}{suffix}"
        existing = Gift.objects.filter(name__iexact=candidate)
        if exclude_id is not None:
            existing = existing.exclude(id=exclude_id)
        if not existing.exists():
            return candidate
        counter += 1


def build_unique_gift_slug(name: str, *, exclude_id: int | None = None) -> str:
    base_slug = slugify(name)[:100] or f"gift-{timezone.now().timestamp()}"
    candidate = base_slug
    counter = 2
    while True:
        existing = Gift.objects.filter(slug=candidate)
        if exclude_id is not None:
            existing = existing.exclude(id=exclude_id)
        if not existing.exists():
            return candidate
        suffix = f"-{counter}"
        candidate = f"{base_slug[: max(1, 100 - len(suffix))]}{suffix}"
        counter += 1


def _extension_from_image_response(response: httpx.Response, image_url: str) -> str:
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in HOTLINE_IMAGE_ALLOWED_TYPES:
        return HOTLINE_IMAGE_ALLOWED_TYPES[content_type]

    extension = mimetypes.guess_extension(content_type)
    if extension in HOTLINE_IMAGE_ALLOWED_TYPES.values():
        return extension

    guessed_type, _ = mimetypes.guess_type(image_url)
    if guessed_type in HOTLINE_IMAGE_ALLOWED_TYPES:
        return HOTLINE_IMAGE_ALLOWED_TYPES[guessed_type]

    return ".jpg"


def _download_hotline_url(url: str) -> httpx.Response:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    with httpx.Client(timeout=HOTLINE_IMAGE_TIMEOUT, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        return response


def _hotline_product_url_candidates(product_url: str) -> list[str]:
    if not product_url:
        return []

    urls = [product_url]
    parsed = urlsplit(product_url)
    if parsed.netloc != "hotline.ua":
        return urls

    path = parsed.path or "/"
    if path.startswith("/ua/"):
        alternate_path = path[3:] or "/"
    else:
        alternate_path = f"/ua{path if path.startswith('/') else f'/{path}'}"

    alternate_url = urlunsplit(parsed._replace(path=alternate_path))
    if alternate_url not in urls:
        urls.append(alternate_url)
    return urls


def resolve_hotline_product_image_url(product_url: str, fallback_url: str | None = None) -> str | None:
    errors = []
    for candidate_url in _hotline_product_url_candidates(product_url):
        try:
            response = _download_hotline_url(candidate_url)
        except httpx.HTTPError as exc:
            errors.append(f"{candidate_url}: {exc}")
            continue

        match = HOTLINE_OG_IMAGE_PATTERN.search(response.text)
        if match:
            return unescape(match.group(1))

    if errors:
        logger.warning(
            "[hotline] failed to resolve product image url product_url=%s errors=%s",
            product_url,
            "; ".join(errors),
        )
    return fallback_url


def _hotline_image_url_candidates(image_url: str) -> list[str]:
    urls = [image_url]
    match = HOTLINE_TX_PLACEHOLDER_IMAGE_PATTERN.match(image_url)
    if match:
        alternate_url = f"{match.group('prefix')}5{match.group('suffix')}"
        if alternate_url not in urls:
            urls.append(alternate_url)
    return urls


def _website_favicon_candidates(website: str | None) -> list[str]:
    if not website:
        return []

    parsed = urlsplit(_canonical_website(website))
    if not parsed.netloc:
        return []

    origin = urlunsplit((parsed.scheme or "https", parsed.netloc, "", "", ""))
    return [
        f"{origin}/favicon.ico",
        f"{origin}/favicon.png",
        f"{origin}/apple-touch-icon.png",
    ]


def cache_shop_logo(
    shop: Shop,
    logo_url: str | None = None,
    *,
    seller_website: str | None = None,
    use_favicon: bool = True,
) -> bool:
    if shop.logo:
        return False

    candidates = []
    if logo_url:
        candidates.append(logo_url)
    if use_favicon:
        for candidate in _website_favicon_candidates(seller_website or shop.website):
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate_url in candidates:
        try:
            response = _download_hotline_url(candidate_url)
        except httpx.HTTPError as exc:
            logger.info(
                "[hotline] failed to download shop logo shop=%s url=%s error=%s",
                shop.id or shop.slug,
                candidate_url,
                exc,
            )
            continue

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in HOTLINE_IMAGE_ALLOWED_TYPES:
            logger.info(
                "[hotline] skipped unsupported shop logo type shop=%s url=%s content_type=%s",
                shop.id or shop.slug,
                candidate_url,
                content_type,
            )
            continue

        content = response.content
        if not content or len(content) > HOTLINE_IMAGE_MAX_BYTES:
            logger.info(
                "[hotline] skipped invalid shop logo size shop=%s url=%s size=%d",
                shop.id or shop.slug,
                candidate_url,
                len(content),
            )
            continue

        extension = _extension_from_image_response(response, candidate_url)
        filename = f"hotline/{shop.slug}{extension}"
        try:
            shop.logo.save(filename, ContentFile(content), save=False)
        except Exception as exc:
            logger.warning(
                "[hotline] failed to store shop logo shop=%s url=%s error=%s",
                shop.id or shop.slug,
                candidate_url,
                exc,
            )
            shop.logo = ""
            continue

        logger.info("[hotline] cached shop logo shop=%s file=%s", shop.id, shop.logo.name)
        return True

    return False


def cache_hotline_gift_image(
    gift: Gift,
    image_url: str | None,
    *,
    product_url: str = "",
) -> bool:
    if gift.image:
        return False

    resolved_url = resolve_hotline_product_image_url(product_url, image_url)
    if not resolved_url:
        return False

    for candidate_url in _hotline_image_url_candidates(resolved_url):
        try:
            response = _download_hotline_url(candidate_url)
        except httpx.HTTPError as exc:
            logger.warning(
                "[hotline] failed to download gift image gift=%s url=%s error=%s",
                gift.id or gift.source_product_id,
                candidate_url,
                exc,
            )
            continue

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in HOTLINE_IMAGE_ALLOWED_TYPES:
            logger.warning(
                "[hotline] skipped unsupported gift image type gift=%s url=%s content_type=%s",
                gift.id or gift.source_product_id,
                candidate_url,
                content_type,
            )
            continue

        content = response.content
        if not content or len(content) > HOTLINE_IMAGE_MAX_BYTES:
            logger.warning(
                "[hotline] skipped invalid gift image size gift=%s url=%s size=%d",
                gift.id or gift.source_product_id,
                candidate_url,
                len(content),
            )
            continue
        if content_type == "image/gif" and len(content) <= HOTLINE_PLACEHOLDER_MAX_BYTES:
            logger.warning(
                "[hotline] skipped likely placeholder gift image gift=%s url=%s size=%d",
                gift.id or gift.source_product_id,
                candidate_url,
                len(content),
            )
            continue

        source_product_id = gift.source_product_id or "unknown"
        extension = _extension_from_image_response(response, candidate_url)
        filename = f"hotline/{source_product_id}{extension}"
        try:
            gift.image.save(filename, ContentFile(content), save=False)
        except Exception as exc:
            logger.warning(
                "[hotline] failed to store gift image gift=%s url=%s error=%s",
                gift.id or gift.source_product_id,
                candidate_url,
                exc,
            )
            gift.image = ""
            continue

        logger.info("[hotline] cached gift image gift=%s file=%s", gift.id, gift.image.name)
        return True

    return False


def upsert_hotline_gift_from_summary(seed: HotlineSeed, summary) -> tuple[Gift, bool, bool]:
    category = ensure_hotline_category(seed)
    source_product_id = str(summary.external_product_id).strip()
    if not source_product_id:
        raise ValueError("Hotline summary is missing source_product_id")

    gift = Gift.objects.filter(
        catalog_source=HOTLINE_CATALOG_SOURCE,
        source_product_id=source_product_id,
    ).first()
    created = gift is None

    price_floor = summary.price
    price_ceiling = summary.original_price or summary.price
    if price_ceiling < price_floor:
        price_ceiling = price_floor

    if gift is None:
        name = build_unique_gift_name(summary.title, source_product_id=source_product_id)
        gift = Gift(
            name=name,
            slug=build_unique_gift_slug(name),
            gender="U",
            age_min=0,
            age_max=100,
        )

    changed_fields: list[str] = []
    desired_name = build_unique_gift_name(
        summary.title,
        source_product_id=source_product_id,
        exclude_id=gift.id,
    )
    if gift.name != desired_name:
        gift.name = desired_name
        changed_fields.append("name")

    desired_slug = build_unique_gift_slug(desired_name, exclude_id=gift.id)
    if gift.slug != desired_slug:
        gift.slug = desired_slug
        changed_fields.append("slug")

    field_values = {
        "category": category,
        "image_url": (summary.image_url or "")[:1000] or None,
        "catalog_source": HOTLINE_CATALOG_SOURCE,
        "source_product_id": source_product_id,
        "source_product_url": summary.product_url[:500],
        "min_price": price_floor,
        "max_price": price_ceiling,
        "is_active": True,
    }
    for field, value in field_values.items():
        if getattr(gift, field) != value:
            setattr(gift, field, value)
            changed_fields.append(field)

    if created:
        cache_hotline_gift_image(
            gift,
            summary.image_url,
            product_url=summary.product_url,
        )
        gift.save()
        assign_hotline_gift_tags(gift, seed, summary.title)
        return gift, True, True

    if cache_hotline_gift_image(
        gift,
        summary.image_url,
        product_url=summary.product_url,
    ):
        changed_fields.append("image")

    if changed_fields:
        gift.save(update_fields=sorted(set(changed_fields + ["updated_at"])))
        assign_hotline_gift_tags(gift, seed, summary.title)
        return gift, False, True
    if assign_hotline_gift_tags(gift, seed, summary.title):
        return gift, False, True
    return gift, False, False


def get_active_ingestion_run(
    task_type: str,
    *,
    seed_key: str = "",
    gift_id: int | None = None,
    source_product_id: str = "",
) -> IngestionRun | None:
    queryset = IngestionRun.objects.filter(
        task_type=task_type,
        status__in=(IngestionRun.STATUS_PENDING, IngestionRun.STATUS_RUNNING),
    )
    if seed_key:
        queryset = queryset.filter(seed_key=seed_key)
    if gift_id is not None:
        queryset = queryset.filter(gift_id=gift_id)
    if source_product_id:
        queryset = queryset.filter(source_product_id=source_product_id)
    return queryset.order_by("-created_at", "-id").first()


def create_ingestion_run(
    task_type: str,
    *,
    seed_key: str = "",
    gift: Gift | None = None,
    source_product_id: str = "",
) -> IngestionRun:
    return IngestionRun.objects.create(
        task_type=task_type,
        seed_key=seed_key,
        gift=gift,
        source_product_id=source_product_id or (gift.source_product_id if gift else ""),
        status=IngestionRun.STATUS_PENDING,
    )


def mark_ingestion_run_running(run: IngestionRun) -> IngestionRun:
    run.status = IngestionRun.STATUS_RUNNING
    run.started_at = timezone.now()
    run.finished_at = None
    run.last_error = ""
    run.save(update_fields=["status", "started_at", "finished_at", "last_error", "updated_at"])
    return run


def mark_ingestion_run_completed(run: IngestionRun, *, discovered_count: int = 0, updated_count: int = 0) -> IngestionRun:
    run.status = IngestionRun.STATUS_COMPLETED
    run.discovered_count = discovered_count
    run.updated_count = updated_count
    run.finished_at = timezone.now()
    run.last_error = ""
    run.save(
        update_fields=[
            "status",
            "discovered_count",
            "updated_count",
            "finished_at",
            "last_error",
            "updated_at",
        ],
    )
    return run


def mark_ingestion_run_failed(run: IngestionRun, exc: Exception) -> IngestionRun:
    run.status = IngestionRun.STATUS_FAILED
    run.finished_at = timezone.now()
    run.last_error = str(exc)
    run.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
    return run


def get_stale_hotline_gifts(limit: int = 100, *, older_than: timedelta | None = None) -> list[Gift]:
    older_than = older_than or timedelta(hours=24)
    cutoff = timezone.now() - older_than
    queryset = (
        Gift.objects.filter(catalog_source=HOTLINE_CATALOG_SOURCE, is_active=True)
        .annotate(last_offer_check=Max("productlinks__last_checked"))
        .filter(Q(productlinks__isnull=True) | Q(last_offer_check__isnull=True) | Q(last_offer_check__lt=cutoff))
        .distinct()
        .order_by("updated_at", "id")
    )
    return list(queryset[:limit])


def _canonical_website(value: str | None) -> str:
    if not value:
        return "https://hotline.ua"
    normalized = value.strip().rstrip("/")
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    return f"https://{normalized.lstrip('/')}"


def build_unique_shop_slug(
    name: str,
    *,
    seller_external_id: str | None = None,
    website: str | None = None,
    exclude_id: int | None = None,
) -> str:
    website_slug = ""
    if website:
        website_slug = slugify(re.sub(r"^https?://", "", website).split("/")[0])

    base_slug = (
        slugify(seller_external_id or "")
        or website_slug
        or slugify(name)
        or f"shop-{timezone.now().timestamp()}"
    )[:100]
    candidate = base_slug
    counter = 2
    while True:
        existing = Shop.objects.filter(slug=candidate)
        if exclude_id is not None:
            existing = existing.exclude(id=exclude_id)
        if not existing.exists():
            return candidate
        suffix = f"-{counter}"
        candidate = f"{base_slug[: max(1, 100 - len(suffix))]}{suffix}"
        counter += 1


def get_or_create_hotline_merchant_shop(offer) -> Shop:
    website = _canonical_website(getattr(offer, "seller_url", None))

    shop = None
    seller_name = (offer.seller_name or "").strip()
    if seller_name:
        shop = Shop.objects.filter(name__iexact=seller_name).first()
    if shop is None and website != "https://hotline.ua":
        shop = Shop.objects.filter(website__iexact=website).first()

    if shop is not None:
        update_fields: list[str] = []
        if website and shop.website != website:
            shop.website = website
            update_fields.append("website")
        if not shop.is_active:
            shop.is_active = True
            update_fields.append("is_active")
        if cache_shop_logo(
            shop,
            getattr(offer, "seller_logo_url", None),
            seller_website=website,
            use_favicon=False,
        ):
            update_fields.append("logo")
        if update_fields:
            shop.save(update_fields=update_fields + ["updated_at"])
        return shop

    name = build_unique_shop_name(seller_name or website)
    shop = Shop.objects.create(
        name=name,
        slug=build_unique_shop_slug(
            name,
            seller_external_id=getattr(offer, "seller_external_id", None),
            website=website,
        ),
        website=website,
        shop_type="specialized",
        specialization="Hotline merchant",
        is_active=True,
    )
    if cache_shop_logo(
        shop,
        getattr(offer, "seller_logo_url", None),
        seller_website=website,
        use_favicon=False,
    ):
        shop.save(update_fields=["logo", "updated_at"])
    return shop


def build_unique_shop_name(value: str, *, exclude_id: int | None = None) -> str:
    base_name = re.sub(r"\s+", " ", (value or "").strip())[:100] or "Hotline merchant"
    existing = Shop.objects.filter(name__iexact=base_name)
    if exclude_id is not None:
        existing = existing.exclude(id=exclude_id)
    if not existing.exists():
        return base_name

    counter = 2
    while True:
        suffix = f" ({counter})"
        candidate = f"{base_name[: max(1, 100 - len(suffix))]}{suffix}"
        existing = Shop.objects.filter(name__iexact=candidate)
        if exclude_id is not None:
            existing = existing.exclude(id=exclude_id)
        if not existing.exists():
            return candidate
        counter += 1


def upsert_hotline_merchant_offer(gift: Gift, shop: Shop, offer) -> tuple[ProductLink, bool, bool]:
    now = timezone.now()
    normalized_url = normalize_product_url(offer.product_url)
    existing = ProductLink.objects.filter(
        shop=shop,
        external_offer_id=offer.external_offer_id,
    ).first()
    previous_snapshot = None
    if existing is not None:
        previous_snapshot = {
            "gift_id": existing.gift_id,
            "product_url": existing.product_url,
            "product_name": existing.product_name,
            "price": existing.price,
            "original_price": existing.original_price,
            "in_stock": existing.in_stock,
            "seller_name": existing.seller_name,
            "seller_external_id": existing.seller_external_id,
            "seller_url": existing.seller_url,
        }

    link, created = ProductLink.objects.update_or_create(
        shop=shop,
        external_offer_id=offer.external_offer_id,
        defaults={
            "gift": gift,
            "product_url": offer.product_url,
            "normalized_product_url": normalized_url,
            "product_name": offer.title[:300],
            "price": offer.price,
            "original_price": offer.original_price,
            "in_stock": True,
            "image_url": (offer.image_url or gift.image_url or "")[:1000] or None,
            "last_checked": now,
            "last_price_update": now,
            "external_product_id": offer.external_product_id,
            "seller_name": (offer.seller_name or shop.name)[:200],
            "seller_external_id": (offer.seller_external_id or "")[:200] or None,
            "seller_url": offer.seller_url or shop.website,
            "is_marketplace_offer": True,
        },
    )

    latest_snapshot = (
        PriceHistory.objects.filter(product_link=link)
        .order_by("-recorded_at")
        .values("price", "in_stock")
        .first()
    )
    if created or latest_snapshot != {"price": offer.price, "in_stock": True}:
        PriceHistory.objects.create(
            product_link=link,
            price=offer.price,
            in_stock=True,
        )

    current_snapshot = {
        "gift_id": link.gift_id,
        "product_url": link.product_url,
        "product_name": link.product_name,
        "price": link.price,
        "original_price": link.original_price,
        "in_stock": link.in_stock,
        "seller_name": link.seller_name,
        "seller_external_id": link.seller_external_id,
        "seller_url": link.seller_url,
    }
    changed = created or previous_snapshot != current_snapshot
    return link, created, changed


def mark_missing_hotline_offers_inactive(gift: Gift, active_offer_ids: set[str]) -> int:
    now = timezone.now()
    queryset = ProductLink.objects.filter(
        gift=gift,
        is_marketplace_offer=True,
        external_product_id=gift.source_product_id,
    )
    if active_offer_ids:
        queryset = queryset.exclude(external_offer_id__in=active_offer_ids)

    stale_count = 0
    for link in queryset:
        snapshot_changed = link.in_stock
        link.in_stock = False
        link.last_checked = now
        link.save(update_fields=["in_stock", "last_checked", "updated_at"])
        if snapshot_changed:
            PriceHistory.objects.create(
                product_link=link,
                price=link.price,
                in_stock=False,
            )
            stale_count += 1
    return stale_count


def refresh_shop_product_counts(shop_ids: set[int]) -> None:
    for shop_id in shop_ids:
        Shop.objects.filter(id=shop_id).update(
            total_products=ProductLink.objects.filter(shop_id=shop_id).count(),
        )


def sync_hotline_product_offers(gift: Gift, offers: list) -> dict[str, int]:
    active_offer_ids: set[str] = set()
    touched_shop_ids: set[int] = set()
    updated_count = 0

    with suppress_productlink_price_cache_updates():
        for offer in offers:
            shop = get_or_create_hotline_merchant_shop(offer)
            touched_shop_ids.add(shop.id)
            active_offer_ids.add(offer.external_offer_id)
            _, _, changed = upsert_hotline_merchant_offer(gift=gift, shop=shop, offer=offer)
            if changed:
                updated_count += 1

        stale_count = mark_missing_hotline_offers_inactive(gift, active_offer_ids)

    refresh_shop_product_counts(touched_shop_ids)
    refresh_gift_price_cache(gift.id)
    return {
        "offers_count": len(offers),
        "updated_count": updated_count + stale_count,
        "stale_count": stale_count,
    }


def base_gift_queryset() -> QuerySet[Gift]:
    best_offer_subquery = ProductLink.objects.filter(
        gift_id=OuterRef("pk"),
        in_stock=True,
    ).order_by("price", "id")

    return (
        Gift.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("tags")
        .annotate(best_offer_id=Subquery(best_offer_subquery.values("id")[:1]))
    )


def apply_gift_filters(qs: QuerySet[Gift], params) -> QuerySet[Gift]:
    query = normalize_search_query(params.get("q", ""))
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(short_description__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
            | Q(tags__name__icontains=query)
        ).distinct()

    category_id = params.get("category")
    if category_id and str(category_id).isdigit():
        qs = qs.filter(category_id=int(category_id))

    gender = params.get("gender")
    if gender in ("M", "F"):
        qs = qs.filter(gender__in=(gender, "U"))

    age = params.get("age")
    if age and str(age).isdigit():
        age_value = int(age)
        qs = qs.filter(age_min__lte=age_value, age_max__gte=age_value)

    budget_min = params.get("budget_min")
    if budget_min:
        try:
            qs = qs.filter(min_price__gte=Decimal(str(budget_min)))
        except Exception:
            pass

    budget_max = params.get("budget_max")
    if budget_max:
        try:
            qs = qs.filter(min_price__lte=Decimal(str(budget_max)))
        except Exception:
            pass

    tag_ids = params.get("tags")
    if tag_ids:
        ids = [int(value) for value in str(tag_ids).split(",") if value.isdigit()]
        if ids:
            qs = qs.filter(tags__id__in=ids).distinct()

    return qs.order_by("-popularity_score", "-created_at")


def serialize_gift_results(qs: QuerySet[Gift], limit: int = MAX_RESULTS) -> tuple[list[dict], int]:
    limited_gifts = list(qs[:limit])
    offer_ids = [gift.best_offer_id for gift in limited_gifts if gift.best_offer_id]
    best_offers = {
        offer.id: offer
        for offer in ProductLink.objects.select_related("shop").filter(id__in=offer_ids)
    }

    results: list[dict] = []
    for gift in limited_gifts:
        best_offer = best_offers.get(gift.best_offer_id)
        results.append(
            {
                "id": gift.id,
                "title": gift.name,
                "slug": gift.slug,
                "detail_url": reverse("gifts:gift_detail", args=[gift.slug]),
                "short_description": gift.short_description or "",
                "image": (
                    gift.image.url
                    if gift.image
                    else gift.image_url or (best_offer.image_url if best_offer else "")
                ),
                "category": gift.category.name if gift.category else "",
                "min_price": str(gift.min_price or 0),
                "max_price": str(gift.max_price or gift.min_price or 0),
                "popularity_score": gift.popularity_score,
                "tags": [tag.name for tag in gift.tags.all()],
                "best_offer": (
                    {
                        "id": best_offer.id,
                        "shop": best_offer.shop.name,
                        "seller_name": best_offer.seller_name or best_offer.shop.name,
                        "price": str(best_offer.price),
                        "original_price": (
                            str(best_offer.original_price)
                            if best_offer.original_price
                            else None
                        ),
                        "product_url": best_offer.product_url,
                        "image_url": best_offer.image_url,
                        "is_marketplace_offer": best_offer.is_marketplace_offer,
                    }
                    if best_offer
                    else None
                ),
                "best_offer_url": best_offer.product_url if best_offer else "",
            },
        )

    return results, len(results)
