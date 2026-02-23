from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ProductLink


@receiver(post_save, sender=ProductLink)
def productlink_post_save(sender, instance, **kwargs):
    """After a ProductLink is saved, refresh the Gift price cache."""
    from .tasks import update_gift_price_cache
    update_gift_price_cache.delay(instance.gift_id)
