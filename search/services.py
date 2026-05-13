from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone
from django.utils.text import slugify
from rapidfuzz import fuzz

from gifts.models import Category, Gift
from shops.models import PriceHistory, ProductLink, Shop, normalize_product_url

from .models import IngestionRun, SearchIngestionJob
from .seeds import HotlineSeed

logger = logging.getLogger("search.services")

SEARCH_JOB_TTL = timedelta(hours=6)
MAX_RESULTS = 20
HOTLINE_CATALOG_SOURCE = Gift.CATALOG_SOURCE_HOTLINE


def normalize_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def extract_request_filters(data) -> dict:
    filters: dict[str, str] = {}
    for key in ("category", "gender", "age", "budget_min", "budget_max", "tags"):
        value = data.get(key)
        if value not in (None, ""):
            filters[key] = str(value)
    return filters


def get_or_queue_hotline_job(query: str, request_filters: dict) -> SearchIngestionJob:
    normalized_query = normalize_search_query(query)
    if not normalized_query:
        raise ValueError("Search query must not be empty")

    now = timezone.now()
    trimmed_query = re.sub(r"\s+", " ", query.strip())
    should_enqueue = False

    with transaction.atomic():
        job, created = SearchIngestionJob.objects.select_for_update().get_or_create(
            source=SearchIngestionJob.SOURCE_HOTLINE,
            normalized_query=normalized_query,
            defaults={
                "query": trimmed_query,
                "request_filters": request_filters,
                "status": SearchIngestionJob.STATUS_PENDING,
                "last_requested_at": now,
                "queued_at": now,
            },
        )

        if created:
            should_enqueue = True
        else:
            update_fields = ["query", "request_filters", "last_requested_at"]
            job.query = trimmed_query
            job.request_filters = request_filters
            job.last_requested_at = now

            if job.status in (
                SearchIngestionJob.STATUS_PENDING,
                SearchIngestionJob.STATUS_RUNNING,
            ):
                job.save(update_fields=update_fields)
            elif job.is_fresh(SEARCH_JOB_TTL):
                job.save(update_fields=update_fields)
            else:
                job.status = SearchIngestionJob.STATUS_PENDING
                job.last_error = ""
                job.queued_at = now
                job.started_at = None
                job.finished_at = None
                update_fields.extend(
                    ["status", "last_error", "queued_at", "started_at", "finished_at"],
                )
                job.save(update_fields=update_fields)
                should_enqueue = True

    if should_enqueue:
        from .tasks import ingest_hotline_search

        ingest_hotline_search.delay(job.id)

    return job


def get_hotline_shop() -> Shop:
    shop, _ = Shop.objects.get_or_create(
        slug="hotline",
        defaults={
            "name": "Hotline",
            "website": "https://hotline.ua",
            "shop_type": "marketplace",
            "specialization": "Aggregator",
            "priority": 100,
            "is_active": True,
        },
    )
    return shop


def match_or_create_gift(title: str, price: Decimal, image_url: str | None = None) -> Gift:
    exact_match = Gift.objects.filter(name__iexact=title).first()
    if exact_match is not None:
        return exact_match

    best_gift = None
    best_score = 0.0
    for gift in Gift.objects.filter(is_active=True).only("id", "name"):
        score = fuzz.token_sort_ratio(title, gift.name)
        if score > best_score:
            best_score = score
            best_gift = gift

    if best_gift is not None and best_score >= 85:
        return best_gift

    unique_name = build_unique_gift_name(title)
    slug = build_unique_gift_slug(unique_name)
    gift = Gift.objects.create(
        name=unique_name,
        slug=slug,
        is_active=False,
        min_price=price,
        max_price=price,
        image_url=(image_url or "")[:1000] or None,
    )
    logger.info("[hotline] created inactive gift id=%d name=%s", gift.id, gift.name)
    return gift


def upsert_hotline_offer(shop: Shop, gift: Gift, offer) -> tuple[ProductLink, bool]:
    normalized_url = normalize_product_url(offer.product_url)
    now = timezone.now()

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
            "image_url": (offer.image_url or "")[:1000] or None,
            "last_checked": now,
            "last_price_update": now,
            "external_product_id": offer.external_product_id,
            "seller_name": (offer.seller_name or "")[:200] or None,
            "seller_url": offer.seller_url,
            "is_marketplace_offer": False,
            "original_category_name": (offer.category_hint or "")[:300],
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

    return link, created


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
    category, _ = Category.objects.get_or_create(
        slug=seed.category_slug,
        defaults={
            "name": seed.category_name,
            "description": f"Hotline seed category: {seed.category_name}",
            "is_active": True,
        },
    )
    return category


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
        gift.save()
        return gift, True, True

    if changed_fields:
        gift.save(update_fields=sorted(set(changed_fields + ["updated_at"])))
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
            },
        )

    return results, len(results)
