import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gifts", "0004_gift_catalog_source_and_source_product_fields"),
        ("search", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="HotlineSeed",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=100, unique=True)),
                ("query", models.CharField(max_length=200)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("priority", models.PositiveIntegerField(db_index=True, default=100)),
                ("last_queued_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hotline_seeds",
                        to="gifts.category",
                    ),
                ),
            ],
            options={
                "ordering": ["priority", "key"],
            },
        ),
        migrations.AddIndex(
            model_name="hotlineseed",
            index=models.Index(fields=["is_active", "priority"], name="search_hotl_is_acti_f49a8e_idx"),
        ),
        migrations.AddIndex(
            model_name="hotlineseed",
            index=models.Index(fields=["category", "is_active"], name="search_hotl_categor_6e8359_idx"),
        ),
    ]
