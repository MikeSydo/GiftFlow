from __future__ import annotations

import logging
import mimetypes
import re
from html import unescape
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from gifts.default_tags import seed_default_tags
from gifts.models import Category, Gift, Tag

from .models import HotlineSeed, IngestionRun

logger = logging.getLogger("parsing.services")

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
HOTLINE_META_TAG_PATTERN = re.compile(r"<meta\b[^>]*>", re.I)
HOTLINE_META_ATTR_PATTERN = re.compile(r'([:\w-]+)=["\']([^"\']*)["\']', re.I)
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
    tag_slug = category.parent.slug if category.parent_id and category.parent else category.slug
    wanted: set[tuple[str, str]] = set(HOTLINE_TAG_RULES.get(tag_slug, []))
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

        image_url = extract_hotline_og_image_url(response.text)
        if image_url:
            return image_url

    if errors:
        logger.warning(
            "[hotline] failed to resolve product image url product_url=%s errors=%s",
            product_url,
            "; ".join(errors),
        )
    return fallback_url


def extract_hotline_og_image_url(html: str) -> str | None:
    for tag in HOTLINE_META_TAG_PATTERN.findall(html):
        attrs = {
            key.lower(): unescape(value)
            for key, value in HOTLINE_META_ATTR_PATTERN.findall(tag)
        }
        if attrs.get("property") == "og:image" and attrs.get("content"):
            return attrs["content"]
    return None


def _hotline_image_url_candidates(image_url: str) -> list[str]:
    urls = [image_url]
    match = HOTLINE_TX_PLACEHOLDER_IMAGE_PATTERN.match(image_url)
    if match:
        alternate_url = f"{match.group('prefix')}5{match.group('suffix')}"
        if alternate_url not in urls:
            urls.append(alternate_url)
    return urls


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
    if price_floor is not None and price_ceiling is not None and price_ceiling < price_floor:
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
        "is_active": True,
    }
    if created or price_floor is not None:
        field_values["min_price"] = price_floor
        field_values["max_price"] = price_ceiling
    for field, value in field_values.items():
        if getattr(gift, field) != value:
            setattr(gift, field, value)
            changed_fields.append(field)

    should_cache_image = getattr(summary, "needs_product_refresh", True)

    if created:
        if should_cache_image:
            cache_hotline_gift_image(
                gift,
                summary.image_url,
                product_url=summary.product_url,
            )
        gift.save()
        assign_hotline_gift_tags(gift, seed, summary.title)
        return gift, True, True

    if should_cache_image and cache_hotline_gift_image(
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




