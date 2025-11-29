import pytest

from tests.factories import NotebookFactory, UserFactory


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
