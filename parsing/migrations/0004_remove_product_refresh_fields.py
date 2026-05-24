from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("search", "0003_hotlineseed_source_url"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="ingestionrun",
            name="search_inge_task_ty_850850_idx",
        ),
        migrations.RemoveField(
            model_name="ingestionrun",
            name="gift",
        ),
        migrations.RemoveField(
            model_name="ingestionrun",
            name="source_product_id",
        ),
        migrations.AlterField(
            model_name="ingestionrun",
            name="task_type",
            field=models.CharField(
                choices=[("seed_refresh", "Seed refresh")],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
