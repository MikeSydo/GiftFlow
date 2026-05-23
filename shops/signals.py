from contextlib import contextmanager
from contextvars import ContextVar
import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import ProductLink, Shop


_suppress_price_cache_updates = ContextVar("suppress_price_cache_updates", default=False)
logger = logging.getLogger("shops.signals")


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


def _delete_file(field_file) -> None:
    if not field_file or not field_file.name:
        return

    storage = field_file.storage
    name = field_file.name
    try:
        if storage.exists(name):
            storage.delete(name)
    except Exception as exc:
        logger.warning("Failed to delete media file %s: %s", name, exc)


@receiver(pre_save, sender=Shop)
def shop_pre_save(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_logo_name = ""
        return

    instance._previous_logo_name = (
        Shop.objects.filter(pk=instance.pk)
        .values_list("logo", flat=True)
        .first()
        or ""
    )


@receiver(post_save, sender=Shop)
def shop_post_save(sender, instance, **kwargs):
    previous_name = getattr(instance, "_previous_logo_name", "")
    current_name = instance.logo.name if instance.logo else ""
    if not previous_name or previous_name == current_name:
        return

    previous_logo = instance.logo
    previous_logo.name = previous_name
    _delete_file(previous_logo)


@receiver(post_delete, sender=Shop)
def shop_post_delete(sender, instance, **kwargs):
    _delete_file(instance.logo)
