from django.db import migrations, models
from django.db.models import F


def backfill_dismissed_at(apps, schema_editor):
    """Use the last modification time as the best available dismissal time."""
    Thread = apps.get_model("watch", "Thread")
    Thread.objects.filter(status="dismissed").update(dismissed_at=F("modified"))


class Migration(migrations.Migration):
    dependencies = [
        ("watch", "0007_thread_duplicate_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="thread",
            name="dismissed_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(backfill_dismissed_at, migrations.RunPython.noop),
    ]
