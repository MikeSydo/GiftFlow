from __future__ import annotations

from gifts.models import Category

from .models import HotlineSeed
from .seeds import HOTLINE_SEED_TEMPLATES


def import_hotline_category_templates() -> tuple[int, int]:
    created = 0
    updated = 0
    for template in HOTLINE_SEED_TEMPLATES:
        parent = None
        if template.parent_slug:
            parent, _ = Category.objects.get_or_create(
                slug=template.parent_slug,
                defaults={
                    "name": template.parent_name or template.parent_slug.replace("-", " ").title(),
                    "description": f"Hotline category group: {template.parent_name}",
                    "is_active": True,
                },
            )

        category, _ = Category.objects.get_or_create(
            slug=template.category_slug,
            defaults={
                "name": template.category_name,
                "description": f"Hotline seed category: {template.category_name}",
                "parent": parent,
                "is_active": True,
            },
        )
        changed_category_fields: list[str] = []
        if parent is not None and category.parent_id != parent.id:
            category.parent = parent
            changed_category_fields.append("parent")
        if category.name != template.category_name:
            category.name = template.category_name
            changed_category_fields.append("name")
        if changed_category_fields:
            category.save(update_fields=changed_category_fields)

        seed, was_created = HotlineSeed.objects.get_or_create(
            key=template.key,
            defaults={
                "query": template.query,
                "source_url": template.source_url,
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
            if seed.source_url != template.source_url:
                seed.source_url = template.source_url
                changed_fields.append("source_url")
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


def import_hotline_seed_templates() -> tuple[int, int]:
    return import_hotline_category_templates()
