from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, F


class Shop(models.Model):
    SHOP_TYPES = [
        ('marketplace', 'Marketplace'),
        ('specialized', 'Specialized'),
        ('brand', 'Brand'),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    website = models.URLField(max_length=200)
    shop_type = models.CharField(max_length=20, choices=SHOP_TYPES)
    specialization = models.CharField(max_length=200, blank=True, null=True)
    logo = models.ImageField(upload_to='shops/static/images/', blank=True, null=True)
    primary_color = models.CharField(max_length=7, default='#000000')
    has_affiliate = models.BooleanField(default=False)
    affiliate_parameter = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    total_products = models.IntegerField(default=0)
    click_count = models.IntegerField(default=0)
    conversion_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    categories = models.ManyToManyField('gifts.Category', blank=True)

    class Meta:
        ordering = ['-priority']
        constraints = [
            models.CheckConstraint(
                condition=Q(conversion_rate__gte=0) & Q(conversion_rate__lte=100),
                name='shop_conversion_rate_range',
            ),
        ]
        indexes = [
            models.Index(fields=['shop_type', '-priority']),
            models.Index(fields=['-click_count']),
        ]

    def __str__(self):
        return self.name


class ProductLink(models.Model):
    gift = models.ForeignKey(
        'gifts.Gift', on_delete=models.CASCADE, related_name='productlinks',
    )
    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name='productlinks',
    )
    product_url = models.URLField(max_length=500)
    product_name = models.CharField(max_length=300)
    sku = models.CharField(max_length=100, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
    )
    in_stock = models.BooleanField(default=True)
    stock_quantity = models.IntegerField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_price_update = models.DateTimeField(blank=True, null=True)
    click_count = models.IntegerField(default=0)

    # Category matching fields
    original_category_name = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Original category or breadcrumbs from the source shop',
    )
    category_confidence = models.FloatField(
        blank=True, null=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text='Algorithm confidence score for category match (0-100)',
    )
    needs_category_review = models.BooleanField(
        default=False, db_index=True,
        help_text='Flagged for manual category review',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gift', 'shop'], name='unique_gift_shop',
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0), name='productlink_price_positive',
            ),
            models.CheckConstraint(
                condition=Q(original_price__isnull=True) | Q(original_price__gte=F('price')),
                name='productlink_original_gte_price',
            ),
        ]
        indexes = [
            models.Index(fields=['shop', 'price']),
            models.Index(fields=['in_stock', 'price']),
            models.Index(fields=['last_price_update']),
            models.Index(fields=['gift']),
            models.Index(fields=['needs_category_review', 'shop']),
        ]

    def __str__(self):
        return f'{self.product_name} @ {self.shop.name}'


class PriceHistory(models.Model):
    product_link = models.ForeignKey(
        ProductLink, on_delete=models.CASCADE, related_name='price_history',
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    in_stock = models.BooleanField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['product_link', '-recorded_at']),
        ]

    def __str__(self):
        return f'{self.product_link} — {self.price} UAH @ {self.recorded_at}'


class ShopClick(models.Model):
    product_link = models.ForeignKey(
        ProductLink, on_delete=models.CASCADE, related_name='clicks',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='shop_clicks',
    )
    session_key = models.CharField(max_length=40)
    ip_address = models.GenericIPAddressField()
    user_agent = models.CharField(max_length=300)
    referrer = models.URLField(max_length=500, blank=True, null=True)
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['product_link', '-clicked_at']),
            models.Index(fields=['user', '-clicked_at']),
            models.Index(fields=['-clicked_at']),
            models.Index(fields=['session_key']),
        ]

    def __str__(self):
        return f'Click on {self.product_link} @ {self.clicked_at}'