import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Gift


logger = logging.getLogger("gifts.signals")


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


def _capture_previous_file(instance, field_name: str) -> None:
    if not instance.pk:
        setattr(instance, f"_previous_{field_name}_name", "")
        return

    previous = (
        instance.__class__.objects.filter(pk=instance.pk)
        .values_list(field_name, flat=True)
        .first()
    )
    setattr(instance, f"_previous_{field_name}_name", previous or "")


def _delete_replaced_file(instance, field_name: str) -> None:
    previous_name = getattr(instance, f"_previous_{field_name}_name", "")
    current = getattr(instance, field_name)
    current_name = current.name if current else ""
    if not previous_name or previous_name == current_name:
        return

    previous_file = getattr(instance, field_name)
    previous_file.name = previous_name
    _delete_file(previous_file)


@receiver(pre_save, sender=Gift)
def gift_pre_save(sender, instance, **kwargs):
    _capture_previous_file(instance, "image")


@receiver(post_save, sender=Gift)
def gift_post_save(sender, instance, **kwargs):
    _delete_replaced_file(instance, "image")


@receiver(post_delete, sender=Gift)
def gift_post_delete(sender, instance, **kwargs):
    _delete_file(instance.image)
