from __future__ import annotations

from django.utils.text import slugify

from .models import Tag


DEFAULT_TAGS: tuple[tuple[str, str], ...] = (
    ("Birthday", "O"),
    ("Christmas", "O"),
    ("Wedding", "O"),
    ("Housewarming", "O"),
    ("Tech", "I"),
    ("Gaming", "I"),
    ("Coffee", "I"),
    ("Fitness", "I"),
    ("Travel", "I"),
    ("Pets", "I"),
    ("Creativity", "I"),
    ("Home", "I"),
    ("Beauty", "I"),
    ("Auto", "I"),
    ("Toys", "I"),
    ("Friend", "R"),
    ("Partner", "R"),
    ("Parent", "R"),
    ("Child", "R"),
    ("Colleague", "R"),
    ("Kids", "A"),
    ("Teen", "A"),
    ("Adult", "A"),
)


def seed_default_tags() -> int:
    created = 0
    for name, tag_type in DEFAULT_TAGS:
        _, was_created = Tag.objects.get_or_create(
            slug=slugify(name),
            defaults={
                "name": name,
                "tag_type": tag_type,
            },
        )
        if was_created:
            created += 1
    return created
