from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("search", "0002_hotlineseed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hotlineseed",
            name="query",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="hotlineseed",
            name="source_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
    ]
