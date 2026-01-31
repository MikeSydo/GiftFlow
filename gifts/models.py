from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Bootstrap icon class")
    parent = models.ForeignKey('self', blank=True, null=True, related_name='subcategories',
                               on_delete=models.CASCADE, verbose_name="Parent category")
    order = models.PositiveIntegerField(default=0, verbose_name="Sorting order")
    is_active = models.BooleanField(default=True, verbose_name="Active")

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["parent", "order"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

class Tag(models.Model):
    TAG_TYPES = (
        ("O", "occasion"),
        ("R", "relationship"),
        ("I", "interest"),
        ("A", "age_group"),
    )

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    tag_type = models.CharField(max_length=1, choices=TAG_TYPES, verbose_name="Tag type")

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["tag_type", "name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["tag_type", "name"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

class Gift(models.Model):
    GENDER_TYPES = [
        ("M", "Male"),
        ("F", "Female"),
        ("U", "Unisex"),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    short_description = models.TextField(null=True, blank=True, verbose_name="Short description")
    image = models.ImageField(upload_to="gifts/", null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_TYPES, default="U")
    age_min = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(100)], verbose_name="Age min")
    age_max = models.PositiveIntegerField(default=100, validators=[MaxValueValidator(100)],verbose_name="Age max")
    min_price = models.DecimalField(max_digits=20, decimal_places=2, validators=[MinValueValidator(0)],
                                    null=True, blank=True, verbose_name="Min price")
    max_price = models.DecimalField(max_digits=20, decimal_places=2, validators=[MinValueValidator(0)],
                                    null=True, blank=True, verbose_name="Max price")

    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)

    popularity_score = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, verbose_name="Active")
    is_featured = models.BooleanField(default=False, verbose_name="Featured")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Gift"
        verbose_name_plural = "Gifts"
        ordering = ['-popularity_score', '-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['gender', 'age_min', 'age_max']),
            models.Index(fields=['min_price', 'max_price']),
            models.Index(fields=['-popularity_score', '-created_at']),
            models.Index(fields=['is_active', 'is_featured']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(age_min__lte=models.F('age_max')),
                name='age_min_lte_age_max'
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_occasion_tags(self):
        return self.tags.filter(tag_type='occasion')

    def get_relationship_tags(self):
        return self.tags.filter(tag_type='relationship')

    def get_interest_tags(self):
        return self.tags.filter(tag_type='interest')

class GiftImage(models.Model):
    gift = models.ForeignKey(Gift, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='gifts/gallery/')
    alt_text = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Gift Image"
        verbose_name_plural = "Gift Images"
        ordering = ['order']
        indexes = [
            models.Index(fields=['gift', 'order']),
        ]

    def __str__(self):
        return f"{self.gift.name} - Image {self.order}"