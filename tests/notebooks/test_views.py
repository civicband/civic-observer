import pytest
from django.urls import reverse

from tests.factories import NotebookFactory, UserFactory


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
