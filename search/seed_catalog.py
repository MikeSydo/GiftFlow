from __future__ import annotations

from gifts.models import Category

from .models import HotlineSeed
from .seeds import HOTLINE_SEED_TEMPLATES


def import_hotline_seed_templates() -> tuple[int, int]:
    created = 0
    updated = 0
    for template in HOTLINE_SEED_TEMPLATES:
        category, _ = Category.objects.get_or_create(
            slug=template.category_slug,
            defaults={
                "name": template.category_name,
                "description": f"Hotline seed category: {template.category_name}",
                "is_active": True,
            },
        )
        seed, was_created = HotlineSeed.objects.get_or_create(
            key=template.key,
            defaults={
                "query": template.query,
                "category": category,
                "priority": template.priority,
                "is_active": True,
            },
        )
        if was_created:
            created += 1
        else:
            changed_fields: list[str] = []
            if seed.query != template.query:
                seed.query = template.query
                changed_fields.append("query")
            if seed.category_id != category.id:
                seed.category = category
                changed_fields.append("category")
            if seed.priority != template.priority:
                seed.priority = template.priority
                changed_fields.append("priority")
            if changed_fields:
                seed.save(update_fields=changed_fields + ["updated_at"])
                updated += 1
    return created, updated
