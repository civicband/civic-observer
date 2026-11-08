from django.db import migrations


def forwards(apps, schema_editor):
    from research.notebooks_migration import migrate_notebooks

    migrate_notebooks(apps=apps)


def backwards(apps, schema_editor):
    from research.notebooks_migration import unmigrate_notebooks

    unmigrate_notebooks(apps=apps)


class Migration(migrations.Migration):
    dependencies = [
        ("research", "0003_alter_memo_code_alter_memo_result_set"),
        ("notebooks", "0003_notebookentry"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
