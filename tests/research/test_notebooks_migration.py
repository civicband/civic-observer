import pytest

from research.models import Code, Coding, Memo, Project, ResultSet, SetItem
from research.notebooks_migration import migrate_notebook, migrate_notebooks
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestMigrateNotebook:
    def test_creates_project_with_same_uuid_and_saved_pages_set(self):
        notebook = NotebookFactory(name="Housing watch")

        result = migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert project.owner == notebook.user
        assert project.name == "Housing watch"
        result_set = ResultSet.objects.get(project=project)
        assert result_set.name == "Saved pages"
        assert result_set.origin == ResultSet.Origin.IMPORT
        assert result_set.status == ResultSet.Status.READY
        assert result["project"] == project

    def test_maps_entries_notes_and_tags(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        tag = TagFactory(user=notebook.user, name="housing")
        entry = NotebookEntryFactory(
            notebook=notebook, meeting_page=page, note="Worth revisiting"
        )
        entry.tags.add(tag)

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        item = SetItem.objects.get(result_set__project=project, page=page)
        assert item.disposition == SetItem.Disposition.RELEVANT
        assert item.added_via == SetItem.AddedVia.IMPORT
        assert item.added_at == entry.created
        memo = Memo.objects.get(project=project, page=page)
        assert memo.body == "Worth revisiting"
        code = Code.objects.get(project=project, name="housing")
        assert Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_entry_without_note_creates_no_memo(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        NotebookEntryFactory(notebook=notebook, meeting_page=page, note="")

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert SetItem.objects.filter(result_set__project=project, page=page).exists()
        assert not Memo.objects.filter(project=project).exists()

    def test_is_idempotent(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        NotebookEntryFactory(notebook=notebook, meeting_page=page, note="note")

        migrate_notebook(notebook)
        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert Project.objects.filter(id=notebook.id).count() == 1
        assert ResultSet.objects.filter(project=project).count() == 1
        assert SetItem.objects.filter(result_set__project=project).count() == 1
        assert Memo.objects.filter(project=project).count() == 1
        assert project.events.filter(verb="project.imported").count() == 1

    def test_empty_notebook_creates_empty_set_and_event(self):
        notebook = NotebookFactory()

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert SetItem.objects.filter(result_set__project=project).count() == 0
        event = project.events.get(verb="project.imported")
        assert event.payload["entries"] == 0


@pytest.mark.django_db
class TestMigrateNotebooks:
    def test_migrates_every_notebook_and_orphan_tags(self):
        user = UserFactory()
        first = NotebookFactory(user=user, name="First")
        second = NotebookFactory(user=user, name="Second")
        TagFactory(user=user, name="orphan")

        results = migrate_notebooks([first, second])

        assert set(results) == {str(first.id), str(second.id)}
        assert Project.objects.filter(id=first.id).exists()
        assert Project.objects.filter(id=second.id).exists()
        assert Code.objects.filter(project__owner=user, name="orphan").exists()
        assert not Coding.objects.filter(code__name="orphan").exists()

    def test_orphan_tags_become_codes_without_any_notebooks(self):
        user = UserFactory()
        TagFactory(user=user, name="orphan")

        migrate_notebooks([])

        project = Project.objects.get(owner=user, name="My research")
        assert Code.objects.filter(project=project, name="orphan").exists()

    def test_orphan_tags_are_not_duplicated_across_notebooks(self):
        user = UserFactory()
        first = NotebookFactory(user=user, name="First")
        second = NotebookFactory(user=user, name="Second")
        TagFactory(user=user, name="orphan")

        migrate_notebooks([first, second])

        assert Code.objects.filter(project__owner=user, name="orphan").count() == 1

    def test_unowned_orphan_tags_are_not_silently_dropped(self):
        TagFactory(user=None, name="orphan")

        migrate_notebooks([])

        assert not Code.objects.filter(name="orphan").exists()
