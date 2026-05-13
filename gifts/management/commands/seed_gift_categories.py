from django.core.management.base import BaseCommand

from gifts.models import Category


DEFAULT_CATEGORIES = [
    ("Electronics", "electronics"),
    ("Gaming", "gaming"),
    ("Home & Kitchen", "home-kitchen"),
    ("Beauty", "beauty"),
    ("Fashion", "fashion"),
    ("Accessories", "accessories"),
    ("Sports & Outdoors", "sports-outdoors"),
    ("Auto", "auto"),
    ("Books", "books"),
    ("Toys & Kids", "toys-kids"),
    ("Hobbies & Creativity", "hobbies-creativity"),
    ("Food & Sweets", "food-sweets"),
    ("Experiences", "experiences"),
    ("Travel", "travel"),
    ("Pets", "pets"),
    ("Office", "office"),
]


class Command(BaseCommand):
    help = "Seed a default top-level set of gift categories."

    def handle(self, *args, **options):
        created = 0
        for order, (name, slug) in enumerate(DEFAULT_CATEGORIES):
            _, was_created = Category.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "order": order,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded categories. Created {created}, total default set {len(DEFAULT_CATEGORIES)}."
            )
        )
