"""Release 2 removed the Notebooks models and tables."""

import pytest
from django.apps import apps
from django.db import connection


@pytest.mark.django_db
def test_notebook_tables_are_dropped():
    tables = set(connection.introspection.table_names())

    assert "notebooks_notebook" not in tables
    assert "notebooks_notebookentry" not in tables
    assert "notebooks_notebookentry_tags" not in tables
    assert "notebooks_tag" not in tables


def test_notebooks_app_has_no_models():
    assert list(apps.get_app_config("notebooks").get_models()) == []


def test_notebook_migration_requires_historical_apps():
    from research.notebooks_migration import migrate_notebooks

    with pytest.raises(RuntimeError, match="historical apps"):
        migrate_notebooks([])


@pytest.mark.django_db(transaction=True)
def test_release2_migrates_notebooks_created_after_release1():
    from django.core.management import call_command
    from django.db.migrations.executor import MigrationExecutor

    from tests.factories import (
        MeetingDocumentFactory,
        MeetingPageFactory,
        MuniFactory,
        UserFactory,
    )

    executor = MigrationExecutor(connection)
    executor.migrate([("notebooks", "0003_notebookentry")])
    old_apps = executor.loader.project_state([("notebooks", "0003_notebookentry")]).apps
    Notebook = old_apps.get_model("notebooks", "Notebook")
    NotebookEntry = old_apps.get_model("notebooks", "NotebookEntry")

    user = UserFactory()
    page = MeetingPageFactory(
        document=MeetingDocumentFactory(municipality=MuniFactory())
    )
    notebook = Notebook.objects.create(user_id=user.pk, name="Late notebook")
    NotebookEntry.objects.create(
        notebook_id=notebook.pk,
        meeting_page_id=page.pk,
        note="late note",
    )

    call_command("migrate", "notebooks", verbosity=0)

    from research.models import Memo, Project, SetItem

    project = Project.objects.get(id=notebook.pk)
    assert project.name == "Late notebook"
    assert SetItem.objects.filter(result_set__project=project, page=page).exists()
    assert Memo.objects.filter(project=project, page=page, body="late note").exists()

    tables = set(connection.introspection.table_names())
    assert "notebooks_notebook" not in tables
