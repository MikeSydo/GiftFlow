from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import urlsplit

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .hotline import HotlineAdapter, HotlineChallengeError
from .models import HotlineSeed, IngestionRun
from .services import (
    create_ingestion_run,
    get_active_ingestion_run,
    mark_ingestion_run_completed,
    mark_ingestion_run_failed,
    mark_ingestion_run_running,
    upsert_hotline_gift_from_summary,
)

logger = logging.getLogger("parsing.tasks")

HOTLINE_CHALLENGE_COOLDOWN_MINUTES = getattr(
    settings,
    "HOTLINE_CHALLENGE_COOLDOWN_MINUTES",
    60,
)
HOTLINE_FALLBACK_STOPWORDS = {
    "для",
    "до",
    "від",
    "из",
    "із",
    "і",
    "и",
    "с",
    "со",
    "та",
    "у",
    "в",
    "з",
    "зі",
}
HOTLINE_FALLBACK_STOPWORDS = {
    "\u0434\u043b\u044f",
    "\u0434\u043e",
    "\u0432\u0456\u0434",
    "\u0438\u0437",
    "\u0456\u0437",
    "\u0456",
    "\u0438",
    "\u0441",
    "\u0441\u043e",
    "\u0442\u0430",
    "\u0443",
    "\u0432",
    "\u0437",
    "\u0437\u0456",
}


def active_hotline_seeds():
    return HotlineSeed.objects.select_related("category").filter(
        is_active=True,
        category__parent__isnull=False,
    )


def get_hotline_seed(seed_key: str) -> HotlineSeed:
    return HotlineSeed.objects.select_related("category").get(key=seed_key)


def hotline_seed_fallback_queries(seed: HotlineSeed) -> list[str]:
    candidates = [
        seed.query,
        seed.category.name if seed.category_id and seed.category else "",
    ]
    words = [word.strip(" ,.;:/()[]{}") for word in seed.query.split()]
    meaningful_words = [word for word in words if word and word.lower() not in HOTLINE_FALLBACK_STOPWORDS]
    if meaningful_words:
        candidates.append(meaningful_words[0])
    if len(meaningful_words) >= 2:
        candidates.append(" ".join(meaningful_words[:2]))

    deduped = []
    seen = set()
    for candidate in candidates:
        normalized = " ".join((candidate or "").split())
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(normalized)
    return deduped


def hotline_seed_product_path_prefixes(seed: HotlineSeed) -> tuple[str, ...]:
    if not seed.source_url:
        return ()

    path_parts = [
        part
        for part in urlsplit(seed.source_url).path.strip("/").split("/")
        if part and part != "ua"
    ]
    if len(path_parts) < 2:
        return ()

    section, catalog = path_parts[:2]
    return (
        f"/{section}-{catalog}/",
        f"/{section}/{catalog}/",
    )


def filter_hotline_seed_summaries(seed: HotlineSeed, summaries: list) -> list:
    prefixes = hotline_seed_product_path_prefixes(seed)
    if not prefixes:
        return summaries

    filtered = []
    for summary in summaries:
        product_path = urlsplit(summary.product_url).path
        if any(product_path.startswith(prefix) for prefix in prefixes):
            filtered.append(summary)

    if len(filtered) != len(summaries):
        logger.info(
            "[hotline] filtered fallback suggestions seed=%s kept=%d rejected=%d prefixes=%s",
            seed.key,
            len(filtered),
            len(summaries) - len(filtered),
            ",".join(prefixes),
        )
    return filtered


def hotline_seed_refresh_paused() -> bool:
    if getattr(settings, "HOTLINE_SEARCH_SUGGESTION_FALLBACK", True):
        return False
    if HOTLINE_CHALLENGE_COOLDOWN_MINUTES <= 0:
        return False

    cutoff = timezone.now() - timedelta(minutes=HOTLINE_CHALLENGE_COOLDOWN_MINUTES)
    return IngestionRun.objects.filter(
        task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
        status=IngestionRun.STATUS_FAILED,
        finished_at__gte=cutoff,
        last_error__icontains="captcha/challenge",
    ).exists()


def queue_hotline_seed_refresh(seed: HotlineSeed | str | int) -> IngestionRun | None:
    if hotline_seed_refresh_paused():
        logger.warning(
            "[hotline] seed refresh queueing paused for %d minute(s) after captcha/challenge response",
            HOTLINE_CHALLENGE_COOLDOWN_MINUTES,
        )
        return None

    if isinstance(seed, int):
        seed = HotlineSeed.objects.select_related("category").get(pk=seed)
    if isinstance(seed, str):
        seed = get_hotline_seed(seed)
    if seed.category_id and seed.category.parent_id is None:
        raise ValueError("Hotline seed refresh requires a subcategory.")

    active_run = get_active_ingestion_run(
        IngestionRun.TASK_TYPE_SEED_REFRESH,
        seed_key=seed.key,
    )
    if active_run is not None:
        HotlineSeed.objects.filter(id=seed.id).update(last_queued_at=timezone.now())
        return active_run

    run = create_ingestion_run(
        IngestionRun.TASK_TYPE_SEED_REFRESH,
        seed_key=seed.key,
    )
    HotlineSeed.objects.filter(id=seed.id).update(last_queued_at=timezone.now())
    refresh_hotline_seed.delay(run.id)
    return run


def queue_missing_hotline_seed_refreshes() -> int:
    if hotline_seed_refresh_paused():
        logger.warning("[hotline] cold-start seed refresh queueing skipped during challenge cooldown")
        return 0

    queued = 0
    for seed in active_hotline_seeds():
        completed_exists = IngestionRun.objects.filter(
            task_type=IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
            status=IngestionRun.STATUS_COMPLETED,
        ).exists()
        if completed_exists:
            continue
        active_run = get_active_ingestion_run(
            IngestionRun.TASK_TYPE_SEED_REFRESH,
            seed_key=seed.key,
        )
        if active_run is not None:
            continue

        run = queue_hotline_seed_refresh(seed.key)
        if run is not None:
            queued += 1
    return queued


@shared_task(queue="discovery")
def enqueue_hotline_seed_refreshes() -> int:
    if hotline_seed_refresh_paused():
        logger.warning("[hotline] scheduled seed refresh queueing skipped during challenge cooldown")
        return 0

    queued = 0
    for seed in active_hotline_seeds():
        run = queue_hotline_seed_refresh(seed)
        if run is not None:
            queued += 1
    logger.info("[hotline] queued seed refresh runs=%d", queued)
    return queued


@shared_task(queue="discovery")
def enqueue_missing_hotline_seed_refreshes() -> int:
    try:
        queued = queue_missing_hotline_seed_refreshes()
    except DatabaseError as exc:
        logger.warning("[hotline] cold-start bootstrap skipped until database is ready: %s", exc)
        return 0

    logger.info("[hotline] queued cold-start seed refresh runs=%d", queued)
    return queued


@shared_task(queue="discovery", rate_limit="2/m")
def refresh_hotline_seed(run_id: int) -> None:
    run = IngestionRun.objects.get(id=run_id)
    seed = get_hotline_seed(run.seed_key)

    if hotline_seed_refresh_paused():
        exc = HotlineChallengeError(
            "Skipped Hotline seed refresh because captcha/challenge cooldown is active.",
        )
        mark_ingestion_run_failed(run, exc)
        logger.warning("[hotline] seed refresh skipped during challenge cooldown seed=%s", seed.key)
        return

    mark_ingestion_run_running(run)

    try:
        with HotlineAdapter() as adapter:
            try:
                if seed.source_url:
                    summaries = adapter.search_category(seed.source_url)
                else:
                    summaries = adapter.search(seed.query)
            except HotlineChallengeError:
                summaries = []
                for fallback_query in hotline_seed_fallback_queries(seed):
                    fallback_summaries = adapter.search_suggestions(fallback_query)
                    summaries = filter_hotline_seed_summaries(seed, fallback_summaries)
                    if summaries:
                        logger.info(
                            "[hotline] seed refresh used JSON-RPC fallback query=%s seed=%s",
                            fallback_query,
                            seed.key,
                        )
                        break
                if not summaries:
                    raise HotlineChallengeError(
                        "Hotline JSON-RPC fallback did not return products matching seed category.",
                    )

        updated_count = 0
        for summary in summaries:
            _, _, changed = upsert_hotline_gift_from_summary(seed, summary)
            if changed:
                updated_count += 1

        mark_ingestion_run_completed(
            run,
            discovered_count=len(summaries),
            updated_count=updated_count,
        )
        logger.info(
            "[hotline] seed refresh completed seed=%s discovered=%d updated=%d",
            seed.key,
            len(summaries),
            updated_count,
        )
    except HotlineChallengeError as exc:
        mark_ingestion_run_failed(run, exc)
        logger.warning("[hotline] seed refresh blocked seed=%s: %s", seed.key, exc)
        return
    except Exception as exc:
        mark_ingestion_run_failed(run, exc)
        logger.exception("[hotline] seed refresh failed seed=%s: %s", seed.key, exc)
        raise
