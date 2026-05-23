import logging
import os
from celery import Celery
from celery.signals import beat_init

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gift_idea_generator.settings')

logger = logging.getLogger(__name__)
app = Celery('gift_idea_generator')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@beat_init.connect
def queue_cold_start_bootstrap(**kwargs):
    try:
        app.send_task(
            "search.tasks.enqueue_missing_hotline_seed_refreshes",
            queue="discovery",
        )
    except Exception as exc:
        logger.warning("Cold-start bootstrap dispatch failed: %s", exc)
