from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from gifts.models import Category


class SeedGiftCategoriesCommandTestCase(TestCase):
    def test_seed_command_is_idempotent(self):
        output = StringIO()

        call_command("seed_gift_categories", stdout=output)
        first_count = Category.objects.count()
        call_command("seed_gift_categories", stdout=output)
        second_count = Category.objects.count()

        self.assertEqual(first_count, 16)
        self.assertEqual(second_count, 16)
        self.assertTrue(Category.objects.filter(slug="electronics").exists())
        self.assertTrue(Category.objects.filter(slug="home-kitchen").exists())
