from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q


def normalize_product_url(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(url.strip())
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            query,
            "",
        )
    )


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


class ShopIntegration(models.Model):
    CONNECTOR_TYPES = [
        ("json_api", "JSON API"),
        ("xml_feed", "XML Feed"),
        ("html_search_template", "HTML Search Template"),
        ("marketplace_template", "Marketplace Template"),
    ]

    AUTH_TYPES = [
        ("none", "None"),
        ("bearer_token", "Bearer token"),
        ("api_key_header", "API key in header"),
        ("api_key_query", "API key in query"),
        ("basic", "Basic auth"),
    ]

    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name="integrations",
    )
    connector_type = models.CharField(max_length=50, choices=CONNECTOR_TYPES)
    base_url = models.URLField(max_length=500)
    auth_type = models.CharField(max_length=30, choices=AUTH_TYPES, default="none")
    auth_config = models.JSONField(default=dict, blank=True)
    request_config = models.JSONField(default=dict, blank=True)
    field_mapping = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]
        verbose_name = "Shop integration"
        verbose_name_plural = "Shop integrations"
        indexes = [
            models.Index(fields=["shop", "is_active", "-priority"]),
            models.Index(fields=["connector_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.shop.name} [{self.connector_type}]"


class ShopSource(models.Model):
    SOURCE_TYPES = [
        ("category_url", "Category URL"),
        ("search_template", "Search template"),
        ("feed_url", "Feed URL"),
        ("api_endpoint", "API endpoint"),
        ("seed_query", "Seed query"),
    ]

    integration = models.ForeignKey(
        ShopIntegration, on_delete=models.CASCADE, related_name="sources",
    )
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPES)
    value = models.TextField()
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]
        verbose_name = "Shop source"
        verbose_name_plural = "Shop sources"
        indexes = [
            models.Index(fields=["integration", "is_active", "-priority"]),
            models.Index(fields=["source_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.integration.shop.name}: {self.source_type}"


class ShopCategoryAlias(models.Model):
    STATUS_PENDING = "pending"
    STATUS_MATCHED = "matched"
    STATUS_IGNORED = "ignored"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_MATCHED, "Matched"),
        (STATUS_IGNORED, "Ignored"),
    ]

    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name="category_aliases",
    )
    raw_category = models.CharField(max_length=300)
    normalized_category = models.CharField(max_length=300, db_index=True)
    category = models.ForeignKey(
        "gifts.Category", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="shop_aliases",
    )
    confidence = models.FloatField(
        blank=True, null=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shop category alias"
        verbose_name_plural = "Shop category aliases"
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "raw_category"], name="unique_shop_raw_category",
            ),
        ]
        indexes = [
            models.Index(fields=["shop", "status"]),
            models.Index(fields=["shop", "normalized_category"]),
        ]

    def save(self, *args, **kwargs):
        if not self.normalized_category:
            from .services import _normalise

            self.normalized_category = _normalise(self.raw_category)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shop.name}: {self.raw_category}"


class ProductLink(models.Model):
    gift = models.ForeignKey(
        'gifts.Gift', on_delete=models.CASCADE, related_name='productlinks',
    )
    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name='productlinks',
    )
    product_url = models.URLField(max_length=500)
    normalized_product_url = models.CharField(max_length=500, blank=True, default="", db_index=True)
    product_name = models.CharField(max_length=300)
    sku = models.CharField(max_length=100, blank=True, null=True)
    external_offer_id = models.CharField(max_length=200, blank=True, null=True)
    external_product_id = models.CharField(max_length=200, blank=True, null=True)
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
    image_url = models.URLField(max_length=1000, blank=True, null=True, verbose_name='Image URL')
    seller_name = models.CharField(max_length=200, blank=True, null=True)
    seller_external_id = models.CharField(max_length=200, blank=True, null=True)
    seller_url = models.URLField(max_length=500, blank=True, null=True)
    is_marketplace_offer = models.BooleanField(default=False)
    discovered_via_source = models.ForeignKey(
        ShopSource, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="product_links",
    )

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
        verbose_name = "Product offer"
        verbose_name_plural = "Product offers"
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "external_offer_id"],
                condition=Q(external_offer_id__isnull=False) & ~Q(external_offer_id=""),
                name="unique_shop_external_offer_id",
            ),
            models.UniqueConstraint(
                fields=["shop", "normalized_product_url"],
                condition=~Q(normalized_product_url=""),
                name="unique_shop_normalized_product_url",
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
            models.Index(fields=["gift", "shop", "price"]),
            models.Index(fields=["shop", "seller_name"]),
            models.Index(fields=["shop", "external_product_id"]),
        ]

    def save(self, *args, **kwargs):
        self.normalized_product_url = normalize_product_url(self.product_url)
        super().save(*args, **kwargs)

    @property
    def offer_label(self) -> str:
        return self.seller_name or self.shop.name

    def __str__(self):
        return f"{self.product_name} @ {self.offer_label}"


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
