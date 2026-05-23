from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gifts", "0004_gift_catalog_source_and_source_product_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="name",
            field=models.CharField(max_length=100),
        ),
    ]
