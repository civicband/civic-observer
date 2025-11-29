import pytest
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestNotebookListView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected to login."""
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert response.status_code == 302
        assert "/login" in response.url or "/stagedoor" in response.url

    def test_shows_user_notebooks(self, client):
        """Test that view shows only user's notebooks."""
        user = UserFactory()
        other_user = UserFactory()

        NotebookFactory(user=user, name="My Research")
        NotebookFactory(user=other_user, name="Other Research")

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert response.status_code == 200
        assert "My Research" in response.content.decode()
        assert "Other Research" not in response.content.decode()

    def test_hides_archived_by_default(self, client):
        """Test that archived notebooks are hidden by default."""
        user = UserFactory()
        NotebookFactory(user=user, name="Active Notebook", is_archived=False)
        NotebookFactory(user=user, name="Archived Notebook", is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        content = response.content.decode()
        assert "Active Notebook" in content
        assert "Archived Notebook" not in content

    def test_shows_archived_with_param(self, client):
        """Test that archived notebooks shown when requested."""
        user = UserFactory()
        NotebookFactory(user=user, name="Active Notebook", is_archived=False)
        NotebookFactory(user=user, name="Archived Notebook", is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url + "?show_archived=1")

        content = response.content.decode()
        assert "Active Notebook" in content
        assert "Archived Notebook" in content

    def test_empty_state(self, client):
        """Test empty state message when no notebooks."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert "No notebooks yet" in response.content.decode()


@pytest.mark.django_db
class TestNotebookCreateView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        url = reverse("notebooks:notebook-create")
        response = client.get(url)

        assert response.status_code == 302

    def test_get_shows_form(self, client):
        """Test GET request shows the form."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.get(url)

        assert response.status_code == 200
        assert "form" in response.context

    def test_post_creates_notebook(self, client):
        """Test POST creates a notebook for the user."""
        from notebooks.models import Notebook

        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.post(url, {"name": "New Research"})

        assert response.status_code == 302
        assert Notebook.objects.filter(user=user, name="New Research").exists()

    def test_redirects_to_list_after_create(self, client):
        """Test successful creation redirects to list."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.post(url, {"name": "New Research"})

        assert response.status_code == 302
        assert reverse("notebooks:notebook-list") in response.url


@pytest.mark.django_db
class TestNotebookDetailView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        notebook = NotebookFactory()
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 302

    def test_shows_notebook_entries(self, client):
        """Test view shows notebook entries."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")
        doc = MeetingDocumentFactory(meeting_name="CityCouncil")
        page = MeetingPageFactory(document=doc, text="Budget discussion")
        NotebookEntryFactory(notebook=notebook, meeting_page=page, note="Important!")

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "My Research" in content
        assert "CityCouncil" in content
        assert "Important!" in content

    def test_cannot_view_other_users_notebook(self, client):
        """Test users cannot view other users' notebooks."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 404

    def test_empty_notebook_message(self, client):
        """Test empty notebook shows helpful message."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert "No saved pages" in response.content.decode()


@pytest.mark.django_db
class TestNotebookEditView:
    def test_can_edit_own_notebook(self, client):
        """Test user can edit their own notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="Old Name")

        client.force_login(user)
        url = reverse("notebooks:notebook-edit", args=[notebook.pk])
        response = client.post(url, {"name": "New Name"})

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.name == "New Name"

    def test_cannot_edit_other_users_notebook(self, client):
        """Test user cannot edit another user's notebook."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)

        client.force_login(user)
        url = reverse("notebooks:notebook-edit", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 404


@pytest.mark.django_db
class TestNotebookArchiveView:
    def test_can_archive_notebook(self, client):
        """Test user can archive their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, is_archived=False)

        client.force_login(user)
        url = reverse("notebooks:notebook-archive", args=[notebook.pk])
        response = client.post(url)

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.is_archived is True

    def test_can_unarchive_notebook(self, client):
        """Test user can unarchive their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-archive", args=[notebook.pk])
        response = client.post(url)

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.is_archived is False


@pytest.mark.django_db
class TestNotebookDeleteView:
    def test_can_delete_own_notebook(self, client):
        """Test user can delete their own notebook."""
        from notebooks.models import Notebook

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        notebook_pk = notebook.pk

        client.force_login(user)
        url = reverse("notebooks:notebook-delete", args=[notebook.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not Notebook.objects.filter(pk=notebook_pk).exists()

    def test_get_shows_confirmation(self, client):
        """Test GET shows delete confirmation."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="To Delete")

        client.force_login(user)
        url = reverse("notebooks:notebook-delete", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert "To Delete" in response.content.decode()
