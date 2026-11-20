from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("watch", "0003_nonuscity"),
    ]

    operations = [
        migrations.AddField(
            model_name="subreddit",
            name="classified_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="placealias",
            name="kind",
            field=models.CharField(
                choices=[
                    ("name", "Name"),
                    ("name_state", "Name and state"),
                    ("name_state_name", "Name and state name"),
                    ("name_city", "Name and city"),
                    ("nickname", "Nickname"),
                    ("variant", "Variant"),
                    ("subreddit", "Subreddit"),
                ],
                max_length=16,
            ),
        ),
    ]
