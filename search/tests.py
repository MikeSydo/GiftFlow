from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from gifts.models import Gift, Category, Tag
from shops.models import ProductLink, Shop


class SearchGiftsAPITestCase(TestCase):
    """Tests for search_gifts_api endpoint"""

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create categories
        self.category1 = Category.objects.create(
            name="Electronics",
            slug="electronics",
            is_active=True
        )
        self.category2 = Category.objects.create(
            name="Books",
            slug="books",
            is_active=True
        )

        # Create tags
        self.tag_birthday = Tag.objects.create(
            name="Birthday",
            slug="birthday",
            tag_type="O"  # occasion
        )
        self.tag_tech = Tag.objects.create(
            name="Tech lover",
            slug="tech-lover",
            tag_type="I"  # interest
        )
        self.tag_friend = Tag.objects.create(
            name="Friend",
            slug="friend",
            tag_type="R"  # relationship
        )

        # Create gifts
        self.gift1 = Gift.objects.create(
            name="Smartphone",
            slug="smartphone",
            description="Latest smartphone",
            short_description="Cool phone",
            gender="M",
            age_min=18,
            age_max=50,
            min_price=Decimal("500.00"),
            max_price=Decimal("1000.00"),
            category=self.category1,
            popularity_score=100,
            is_active=True,
            is_featured=True
        )
        self.gift1.tags.add(self.tag_birthday, self.tag_tech)

        self.gift2 = Gift.objects.create(
            name="Headphones",
            slug="headphones",
            description="Wireless headphones",
            short_description="Great sound",
            gender="U",
            age_min=15,
            age_max=60,
            min_price=Decimal("100.00"),
            max_price=Decimal("300.00"),
            category=self.category1,
            popularity_score=80,
            is_active=True,
            is_featured=False
        )
        self.gift2.tags.add(self.tag_tech)

        self.gift3 = Gift.objects.create(
            name="Novel Book",
            slug="novel-book",
            description="Bestselling novel",
            short_description="Great read",
            gender="F",
            age_min=20,
            age_max=70,
            min_price=Decimal("10.00"),
            max_price=Decimal("30.00"),
            category=self.category2,
            popularity_score=60,
            is_active=True,
            is_featured=False
        )
        self.gift3.tags.add(self.tag_birthday, self.tag_friend)

        self.shop1 = Shop.objects.create(
            name="Rozetka",
            slug="rozetka",
            website="https://rozetka.com.ua",
            shop_type="marketplace",
        )
        self.shop2 = Shop.objects.create(
            name="Yabluka",
            slug="yabluka",
            website="https://yabluka.ua",
            shop_type="brand",
        )

        ProductLink.objects.create(
            gift=self.gift1,
            shop=self.shop1,
            product_url="https://rozetka.com.ua/pad-1/",
            product_name="Smartphone offer A",
            price=Decimal("550.00"),
            original_price=Decimal("600.00"),
            in_stock=True,
            seller_name="Seller A",
            external_offer_id="offer-a",
        )
        ProductLink.objects.create(
            gift=self.gift1,
            shop=self.shop2,
            product_url="https://yabluka.ua/pad-1/",
            product_name="Smartphone offer B",
            price=Decimal("530.00"),
            in_stock=True,
            seller_name="Yabluka",
            external_offer_id="offer-b",
        )
        ProductLink.objects.create(
            gift=self.gift2,
            shop=self.shop1,
            product_url="https://rozetka.com.ua/headphones-1/",
            product_name="Headphones offer",
            price=Decimal("120.00"),
            in_stock=True,
            seller_name="Seller C",
            external_offer_id="offer-c",
        )

        # Inactive gift (should not appear in results)
        self.gift4 = Gift.objects.create(
            name="Inactive Gift",
            slug="inactive-gift",
            gender="U",
            age_min=0,
            age_max=100,
            min_price=Decimal("50.00"),
            max_price=Decimal("100.00"),
            category=self.category1,
            is_active=False
        )

    def test_search_api_without_filters(self):
        """Test API returns all active gifts without filters"""
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 3)  # Only active gifts

        # Check gifts are ordered by popularity
        self.assertEqual(data['results'][0]['title'], 'Smartphone')
        self.assertEqual(data['results'][1]['title'], 'Headphones')
        self.assertEqual(data['results'][2]['title'], 'Novel Book')

    def test_search_api_filter_by_category(self):
        """Test filtering by category"""
        response = self.client.get('/search/api/', {
            'category': self.category1.id
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['count'], 2)
        categories = [gift['category'] for gift in data['results']]
        self.assertTrue(all(cat == 'Electronics' for cat in categories))

    def test_search_api_filter_by_gender_male(self):
        """Test filtering by male gender includes male and unisex gifts"""
        response = self.client.get('/search/api/', {
            'gender': 'M'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return Smartphone (M) and Headphones (U)
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Smartphone', titles)
        self.assertIn('Headphones', titles)
        self.assertNotIn('Novel Book', titles)

    def test_search_api_filter_by_gender_female(self):
        """Test filtering by female gender includes female and unisex gifts"""
        response = self.client.get('/search/api/', {
            'gender': 'F'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return Novel Book (F) and Headphones (U)
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Novel Book', titles)
        self.assertIn('Headphones', titles)
        self.assertNotIn('Smartphone', titles)

    def test_search_api_filter_by_age(self):
        """Test filtering by age range"""
        response = self.client.get('/search/api/', {
            'age': '25'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # All three gifts match age 25
        self.assertEqual(data['count'], 3)

    def test_search_api_filter_by_age_young(self):
        """Test filtering by young age"""
        response = self.client.get('/search/api/', {
            'age': '16'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Only Headphones (15-60)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Headphones')

    def test_search_api_filter_by_budget_min(self):
        """Test filtering by minimum budget"""
        response = self.client.get('/search/api/', {
            'budget_min': '150'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return only gifts with min_price >= 150
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Smartphone')

    def test_search_api_filter_by_budget_max(self):
        """Test filtering by maximum budget"""
        response = self.client.get('/search/api/', {
            'budget_max': '200'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return gifts with min_price <= 200
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Headphones', titles)
        self.assertIn('Novel Book', titles)

    def test_search_api_filter_by_budget_range(self):
        """Test filtering by budget range"""
        response = self.client.get('/search/api/', {
            'budget_min': '50',
            'budget_max': '150'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Headphones')

    def test_search_api_filter_by_tags(self):
        """Test filtering by tags"""
        response = self.client.get('/search/api/', {
            'tags': f'{self.tag_birthday.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Smartphone and Novel Book have birthday tag
        self.assertEqual(data['count'], 2)
        titles = [gift['title'] for gift in data['results']]
        self.assertIn('Smartphone', titles)
        self.assertIn('Novel Book', titles)

    def test_search_api_filter_by_multiple_tags(self):
        """Test filtering by multiple tags"""
        response = self.client.get('/search/api/', {
            'tags': f'{self.tag_birthday.id},{self.tag_tech.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return gifts that have any of these tags
        self.assertEqual(data['count'], 3)

    def test_search_api_combined_filters(self):
        """Test combining multiple filters"""
        response = self.client.get('/search/api/', {
            'category': self.category1.id,
            'gender': 'M',
            'age': '30',
            'budget_min': '400',
            'budget_max': '600',
            'tags': f'{self.tag_tech.id}'
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return only Smartphone
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Smartphone')

    def test_search_api_response_structure(self):
        """Test response has correct structure"""
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('results', data)
        self.assertIn('count', data)

        if data['results']:
            gift = data['results'][0]
            self.assertIn('id', gift)
            self.assertIn('title', gift)
            self.assertIn('short_description', gift)
            self.assertIn('image', gift)
            self.assertIn('category', gift)
            self.assertIn('min_price', gift)
            self.assertIn('popularity_score', gift)
            self.assertIn('tags', gift)
            self.assertIn('best_offer', gift)
            self.assertIsInstance(gift['tags'], list)

    def test_search_api_returns_best_offer(self):
        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        smartphone = next(item for item in data["results"] if item["title"] == "Smartphone")

        self.assertIsNotNone(smartphone["best_offer"])
        self.assertEqual(smartphone["best_offer"]["shop"], "Yabluka")
        self.assertEqual(smartphone["best_offer"]["price"], "530.00")

    def test_search_api_method_not_allowed(self):
        """Test POST method returns error"""
        response = self.client.post('/search/api/', {})

        self.assertEqual(response.status_code, 405)
        data = response.json()
        self.assertIn('error', data)

    def test_search_api_invalid_parameters(self):
        """Test API handles invalid parameters gracefully"""
        response = self.client.get('/search/api/', {
            'category': 'invalid',
            'age': 'not_a_number',
            'budget_min': 'abc',
            'tags': 'xyz'
        })

        # Should not crash, just return all gifts
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)

    def test_search_api_max_results_limit(self):
        """Test API limits results to 20 items"""
        # Create 25 gifts
        for i in range(25):
            Gift.objects.create(
                name=f"Gift {i}",
                slug=f"gift-{i}",
                gender="U",
                age_min=0,
                age_max=100,
                min_price=Decimal("10.00"),
                max_price=Decimal("50.00"),
                category=self.category1,
                popularity_score=i,
                is_active=True
            )

        response = self.client.get('/search/api/')

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return max 20 results
        self.assertEqual(data['count'], 20)

    def test_search_api_excludes_inactive_gifts(self):
        """Test API does not return inactive gifts"""
        response = self.client.get('/search/api/')

        data = response.json()
        titles = [gift['title'] for gift in data['results']]

        self.assertNotIn('Inactive Gift', titles)
