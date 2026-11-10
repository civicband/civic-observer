"""Drop the Notebooks tables after the Research Projects import.

Release 1 copied notebooks, entries, notes and tags into research projects.
Notebooks created through the legacy route after that import still need the
same treatment, so this migration runs the import again (it is idempotent) and
then refuses to continue if any notebook still lacks a project with the same
id. It finally removes the models and their tables. The ``notebooks`` app stays
installed with its migrations because ``research.0004`` depends on
``notebooks.0003``; removing the app entirely would break fresh installs.
"""

from django.db import migrations


def migrate_remaining_notebooks(apps, schema_editor):
    from research.notebooks_migration import migrate_notebooks

    migrate_notebooks(apps=apps)

    Notebook = apps.get_model("notebooks", "Notebook")
    Project = apps.get_model("research", "Project")

    missing = list(
        Notebook.objects.exclude(id__in=Project.objects.values("id")).values_list(
            "id", flat=True
        )
    )
    if missing:
        raise RuntimeError(
            f"Refusing to drop notebooks tables: {len(missing)} notebook(s) "
            "have no matching research project"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("notebooks", "0003_notebookentry"),
        ("research", "0004_migrate_notebooks"),
    ]

    operations = [
        migrations.RunPython(migrate_remaining_notebooks, migrations.RunPython.noop),
        migrations.DeleteModel(name="NotebookEntry"),
        migrations.DeleteModel(name="Notebook"),
        migrations.DeleteModel(name="Tag"),
    ]
