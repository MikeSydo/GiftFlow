from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("gifts", "0006_remove_giftimage"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="gift",
            name="gifts_gift_min_pri_5f8b55_idx",
        ),
        migrations.RemoveField(
            model_name="gift",
            name="max_price",
        ),
        migrations.RemoveField(
            model_name="gift",
            name="min_price",
        ),
    ]
