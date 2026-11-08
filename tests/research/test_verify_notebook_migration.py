import pytest
from django.core.management import CommandError, call_command

from research.notebooks_migration import migrate_notebook
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
)


@pytest.mark.django_db
class TestVerifyNotebookMigration:
    def test_reports_matching_counts(self, capsys):
        notebook = NotebookFactory(name="Housing")
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        tag = TagFactory(user=notebook.user, name="housing")
        entry = NotebookEntryFactory(notebook=notebook, meeting_page=page, note="note")
        entry.tags.add(tag)
        migrate_notebook(notebook)

        call_command("verify_notebook_migration")

        captured = capsys.readouterr()
        assert "Housing" in captured.out
        assert "OK" in captured.out

    def test_rejects_an_invalid_notebook_id(self):
        with pytest.raises(CommandError):
            call_command("verify_notebook_migration", "--notebook", "not-a-uuid")

    def test_reports_missing_project(self, capsys):
        NotebookFactory(name="Orphan")

        with pytest.raises(SystemExit):
            call_command("verify_notebook_migration")

        captured = capsys.readouterr()
        assert "MISSING" in captured.out
        assert "Orphan" in captured.out
