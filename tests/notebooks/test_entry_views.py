# tests/notebooks/test_entry_views.py
import pytest
from django.urls import reverse

from tests.factories import (
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestEntryEditView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        notebook = NotebookFactory()
        entry = NotebookEntryFactory(notebook=notebook)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 302

    def test_can_edit_own_entry(self, client):
        """Test user can edit entry in their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook, note="")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "Updated note"})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert entry.note == "Updated note"

    def test_cannot_edit_other_users_entry(self, client):
        """Test user cannot edit entry in another user's notebook."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)
        entry = NotebookEntryFactory(notebook=notebook)

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 404

    def test_can_add_tags_to_entry(self, client):
        """Test user can add tags to entry."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        tag = TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "", "tags": [tag.pk]})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert tag in entry.tags.all()


@pytest.mark.django_db
class TestEntryDeleteView:
    def test_can_delete_own_entry(self, client):
        """Test user can delete entry from their notebook."""
        from notebooks.models import NotebookEntry

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        entry_pk = entry.pk

        client.force_login(user)
        url = reverse("notebooks:entry-delete", args=[notebook.pk, entry.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not NotebookEntry.objects.filter(pk=entry_pk).exists()

    def test_cannot_delete_other_users_entry(self, client):
        """Test user cannot delete entry from another user's notebook."""
        from notebooks.models import NotebookEntry

        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)
        entry = NotebookEntryFactory(notebook=notebook)
        entry_pk = entry.pk

        client.force_login(user)
        url = reverse("notebooks:entry-delete", args=[notebook.pk, entry.pk])
        response = client.post(url)

        assert response.status_code == 404
        assert NotebookEntry.objects.filter(pk=entry_pk).exists()
