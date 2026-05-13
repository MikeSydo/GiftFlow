from io import StringIO

from django.core.management import call_command
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from gifts.models import Category, Gift
from shops.models import ProductLink, Shop


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


class GiftDetailViewTestCase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="Gaming",
            slug="gaming",
            is_active=True,
        )
        self.gift = Gift.objects.create(
            name="Steam Deck 256 GB",
            slug="steam-deck-256-gb",
            category=self.category,
            gender="U",
            age_min=0,
            age_max=100,
            min_price=Decimal("19499.00"),
            max_price=Decimal("20599.00"),
            is_active=True,
        )
        self.shop_fast = Shop.objects.create(
            name="Fast Shop",
            slug="fast-shop",
            website="https://fast-shop.example",
            shop_type="specialized",
            priority=100,
        )
        self.shop_slow = Shop.objects.create(
            name="Slow Shop",
            slug="slow-shop",
            website="https://slow-shop.example",
            shop_type="specialized",
            priority=0,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_slow,
            product_url="https://hotline.ua/go/price/2/",
            product_name="Steam Deck 256 GB",
            price=Decimal("20599.00"),
            in_stock=True,
            external_offer_id="2",
            is_marketplace_offer=True,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_fast,
            product_url="https://hotline.ua/go/price/1/",
            product_name="Steam Deck 256 GB",
            price=Decimal("19499.00"),
            in_stock=True,
            external_offer_id="1",
            is_marketplace_offer=True,
        )
        ProductLink.objects.create(
            gift=self.gift,
            shop=self.shop_fast,
            product_url="https://hotline.ua/go/price/3/",
            product_name="Steam Deck 256 GB",
            price=Decimal("18000.00"),
            in_stock=False,
            external_offer_id="3",
            is_marketplace_offer=True,
        )

    def test_gift_detail_orders_offers_by_stock_then_price(self):
        response = self.client.get(reverse("gifts:gift_detail", args=[self.gift.slug]))

        self.assertEqual(response.status_code, 200)
        offers = list(response.context["offers"])
        self.assertEqual(
            [offer.external_offer_id for offer in offers],
            ["1", "2", "3"],
        )
