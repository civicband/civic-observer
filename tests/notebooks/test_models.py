import pytest

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestNotebookModel:
    def test_create_notebook(self):
        """Test basic notebook creation."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")

        assert notebook.name == "My Research"
        assert notebook.user == user
        assert notebook.is_archived is False
        assert notebook.created is not None
        assert notebook.modified is not None
        assert str(notebook.id)  # UUID is valid

    def test_notebook_str_representation(self):
        """Test string representation."""
        notebook = NotebookFactory(name="Housing Research")
        assert "Housing Research" in str(notebook)

    def test_notebook_is_archived_default_false(self):
        """Test that is_archived defaults to False."""
        notebook = NotebookFactory()
        assert notebook.is_archived is False

    def test_notebook_ordering_by_modified_desc(self):
        """Test that notebooks are ordered by modified date descending."""
        from notebooks.models import Notebook

        user = UserFactory()
        notebook1 = NotebookFactory(user=user, name="First")
        _notebook2 = NotebookFactory(user=user, name="Second")

        # Touch notebook1 to make it more recently modified
        notebook1.name = "First Updated"
        notebook1.save()

        notebooks = list(Notebook.objects.filter(user=user))
        assert notebooks[0].name == "First Updated"
        assert notebooks[1].name == "Second"


@pytest.mark.django_db
class TestTagModel:
    def test_create_tag(self):
        """Test basic tag creation."""
        user = UserFactory()
        tag = TagFactory(user=user, name="budget")

        assert tag.name == "budget"
        assert tag.user == user
        assert str(tag.id)

    def test_tag_str_representation(self):
        """Test string representation."""
        tag = TagFactory(name="transportation")
        assert str(tag) == "transportation"

    def test_tag_unique_per_user(self):
        """Test that tag names are unique per user."""
        from django.db import IntegrityError

        from notebooks.models import Tag

        user = UserFactory()
        TagFactory(user=user, name="budget")

        with pytest.raises(IntegrityError):
            Tag.objects.create(user=user, name="budget")

    def test_different_users_can_have_same_tag_name(self):
        """Test that different users can have tags with same name."""
        user1 = UserFactory()
        user2 = UserFactory()

        tag1 = TagFactory(user=user1, name="budget")
        tag2 = TagFactory(user=user2, name="budget")

        assert tag1.name == tag2.name
        assert tag1.user != tag2.user


@pytest.mark.django_db
class TestNotebookEntryModel:
    def test_create_entry(self):
        """Test basic entry creation."""
        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        entry = NotebookEntryFactory(notebook=notebook, meeting_page=page)

        assert entry.notebook == notebook
        assert entry.meeting_page == page
        assert entry.note == ""
        assert str(entry.id)

    def test_entry_with_note(self):
        """Test entry with optional note."""
        entry = NotebookEntryFactory(note="Important budget discussion")
        assert entry.note == "Important budget discussion"

    def test_entry_with_tags(self):
        """Test entry with tags."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)

        tag1 = TagFactory(user=user, name="budget")
        tag2 = TagFactory(user=user, name="housing")

        entry.tags.add(tag1, tag2)

        assert entry.tags.count() == 2
        assert tag1 in entry.tags.all()
        assert tag2 in entry.tags.all()

    def test_entry_unique_page_per_notebook(self):
        """Test that same page cannot be in same notebook twice."""
        from django.db import IntegrityError

        from notebooks.models import NotebookEntry

        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        with pytest.raises(IntegrityError):
            NotebookEntry.objects.create(notebook=notebook, meeting_page=page)

    def test_same_page_in_different_notebooks(self):
        """Test that same page can be in different notebooks."""
        user = UserFactory()
        notebook1 = NotebookFactory(user=user, name="Research 1")
        notebook2 = NotebookFactory(user=user, name="Research 2")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        entry1 = NotebookEntryFactory(notebook=notebook1, meeting_page=page)
        entry2 = NotebookEntryFactory(notebook=notebook2, meeting_page=page)

        assert entry1.meeting_page == entry2.meeting_page
        assert entry1.notebook != entry2.notebook

    def test_entry_ordering_by_created_desc(self):
        """Test entries ordered by creation date descending."""
        from notebooks.models import NotebookEntry

        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page1 = MeetingPageFactory(document=doc, page_number=1)
        page2 = MeetingPageFactory(document=doc, page_number=2)

        entry1 = NotebookEntryFactory(notebook=notebook, meeting_page=page1)
        entry2 = NotebookEntryFactory(notebook=notebook, meeting_page=page2)

        entries = list(NotebookEntry.objects.filter(notebook=notebook))
        assert entries[0] == entry2  # More recent first
        assert entries[1] == entry1
