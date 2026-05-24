from django.core.validators import MaxValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100)
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

    def get_absolute_url(self):
        if self.parent_id and self.parent:
            return reverse(
                "category_subcategory_detail",
                kwargs={
                    "parent_slug": self.parent.slug,
                    "subcategory_slug": self.slug,
                },
            )
        return reverse(
            "category_parent_detail",
            kwargs={"parent_slug": self.slug},
        )

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
    CATALOG_SOURCE_HOTLINE = "hotline"
    CATALOG_SOURCE_CHOICES = [
        (CATALOG_SOURCE_HOTLINE, "Hotline"),
    ]

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
    image_url = models.URLField(max_length=1000, null=True, blank=True, verbose_name="Image URL")
    gender = models.CharField(max_length=1, choices=GENDER_TYPES, default="U")
    age_min = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(100)], verbose_name="Age min")
    age_max = models.PositiveIntegerField(default=100, validators=[MaxValueValidator(100)],verbose_name="Age max")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)

    popularity_score = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, verbose_name="Active")
    is_featured = models.BooleanField(default=False, verbose_name="Featured")
    catalog_source = models.CharField(
        max_length=20,
        choices=CATALOG_SOURCE_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    source_product_id = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
    )
    source_product_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Gift"
        verbose_name_plural = "Gifts"
        ordering = ['-popularity_score', '-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['gender', 'age_min', 'age_max']),
            models.Index(fields=['-popularity_score', '-created_at']),
            models.Index(fields=['is_active', 'is_featured']),
            models.Index(
                fields=['catalog_source', 'source_product_id'],
                name='gifts_gift_catalog_83bc5e_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(age_min__lte=models.F('age_max')),
                name='age_min_lte_age_max'
            ),
            models.UniqueConstraint(
                fields=['catalog_source', 'source_product_id'],
                condition=~models.Q(catalog_source="") & ~models.Q(source_product_id=""),
                name='unique_catalog_source_product_id',
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_occasion_tags(self):
        return self.tags.filter(tag_type='O')

    def get_relationship_tags(self):
        return self.tags.filter(tag_type='R')

    def get_interest_tags(self):
        return self.tags.filter(tag_type='I')
