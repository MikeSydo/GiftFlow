from __future__ import annotations

import logging

from celery import shared_task

from gifts.models import Gift

from .hotline import HotlineAdapter
from .models import IngestionRun
from .seeds import HOTLINE_SEEDS, get_hotline_seed
from .services import (
    create_ingestion_run,
    get_active_ingestion_run,
    get_stale_hotline_gifts,
    mark_ingestion_run_completed,
    mark_ingestion_run_failed,
    mark_ingestion_run_running,
    sync_hotline_product_offers,
    upsert_hotline_gift_from_summary,
)

logger = logging.getLogger("search.tasks")


def queue_hotline_seed_refresh(seed_key: str) -> IngestionRun:
    active_run = get_active_ingestion_run(
        IngestionRun.TASK_TYPE_SEED_REFRESH,
        seed_key=seed_key,
    )
    if active_run is not None:
        return active_run

    run = create_ingestion_run(
        IngestionRun.TASK_TYPE_SEED_REFRESH,
        seed_key=seed_key,
    )
    refresh_hotline_seed.delay(run.id)
    return run


def queue_hotline_product_refresh(gift: Gift) -> IngestionRun:
    active_run = get_active_ingestion_run(
        IngestionRun.TASK_TYPE_PRODUCT_REFRESH,
        gift_id=gift.id,
        source_product_id=gift.source_product_id,
    )
    if active_run is not None:
        return active_run

    run = create_ingestion_run(
        IngestionRun.TASK_TYPE_PRODUCT_REFRESH,
        gift=gift,
        source_product_id=gift.source_product_id,
    )
    refresh_hotline_product.delay(run.id)
    return run


@shared_task(queue="discovery")
def enqueue_hotline_seed_refreshes() -> int:
    queued = 0
    for seed in HOTLINE_SEEDS:
        queue_hotline_seed_refresh(seed.key)
        queued += 1
    logger.info("[hotline] queued seed refresh runs=%d", queued)
    return queued


@shared_task(queue="discovery", rate_limit="12/m")
def refresh_hotline_seed(run_id: int) -> None:
    run = IngestionRun.objects.select_related("gift").get(id=run_id)
    seed = get_hotline_seed(run.seed_key)
    mark_ingestion_run_running(run)

    try:
        with HotlineAdapter() as adapter:
            summaries = adapter.search(seed.query)

        updated_count = 0
        for summary in summaries:
            gift, _, changed = upsert_hotline_gift_from_summary(seed, summary)
            if changed:
                updated_count += 1
            queue_hotline_product_refresh(gift)

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
    except Exception as exc:
        mark_ingestion_run_failed(run, exc)
        logger.exception("[hotline] seed refresh failed seed=%s: %s", seed.key, exc)
        raise


@shared_task(queue="prices")
def enqueue_stale_hotline_product_refreshes(limit: int = 100) -> int:
    queued = 0
    for gift in get_stale_hotline_gifts(limit=limit):
        queue_hotline_product_refresh(gift)
        queued += 1
    logger.info("[hotline] queued stale product refresh runs=%d", queued)
    return queued


@shared_task(queue="prices", rate_limit="20/m")
def refresh_hotline_product(run_id: int) -> None:
    run = IngestionRun.objects.select_related("gift").get(id=run_id)
    gift = run.gift or Gift.objects.get(
        catalog_source=Gift.CATALOG_SOURCE_HOTLINE,
        source_product_id=run.source_product_id,
    )
    mark_ingestion_run_running(run)

    try:
        with HotlineAdapter() as adapter:
            offers = adapter.fetch_product_offers(
                product_url=gift.source_product_url,
                external_product_id=gift.source_product_id,
            )

        sync_result = sync_hotline_product_offers(gift, offers)
        mark_ingestion_run_completed(
            run,
            discovered_count=sync_result["offers_count"],
            updated_count=sync_result["updated_count"],
        )
        logger.info(
            "[hotline] product refresh completed gift=%d source_product_id=%s offers=%d updated=%d stale=%d",
            gift.id,
            gift.source_product_id,
            sync_result["offers_count"],
            sync_result["updated_count"],
            sync_result["stale_count"],
        )
    except Exception as exc:
        mark_ingestion_run_failed(run, exc)
        logger.exception(
            "[hotline] product refresh failed gift=%s source_product_id=%s: %s",
            gift.id if gift else None,
            run.source_product_id,
            exc,
        )
        raise
