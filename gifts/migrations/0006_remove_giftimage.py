from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0005_allow_duplicate_category_names"),
    ]

    operations = [
        migrations.DeleteModel(name="GiftImage"),
    ]
