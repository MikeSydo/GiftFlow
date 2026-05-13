from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ProductLink


_suppress_price_cache_updates = ContextVar("suppress_price_cache_updates", default=False)


@contextmanager
def suppress_productlink_price_cache_updates():
    token = _suppress_price_cache_updates.set(True)
    try:
        yield
    finally:
        _suppress_price_cache_updates.reset(token)


@receiver(post_save, sender=ProductLink)
def productlink_post_save(sender, instance, **kwargs):
    """After a ProductLink is saved, refresh the Gift price cache."""
    if _suppress_price_cache_updates.get():
        return

    from .tasks import update_gift_price_cache
    update_gift_price_cache.delay(instance.gift_id)
