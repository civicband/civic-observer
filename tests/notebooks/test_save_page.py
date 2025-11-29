# tests/notebooks/test_save_page.py
import pytest
from django.urls import reverse

from notebooks.models import NotebookEntry
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSavePageToNotebook:
    def test_requires_login(self, client):
        """Test unauthenticated users get 302 redirect."""
        url = reverse("notebooks:save-page")
        response = client.post(url, {"page_id": "test"})

        assert response.status_code == 302

    def test_saves_page_to_most_recent_notebook(self, client):
        """Test page saved to most recently used notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert NotebookEntry.objects.filter(
            notebook=notebook,
            meeting_page=page,
        ).exists()

    def test_creates_notebook_if_none_exist(self, client):
        """Test creates default notebook if user has none."""
        from notebooks.models import Notebook

        user = UserFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Notebook.objects.filter(user=user).exists()
        notebook = Notebook.objects.get(user=user)
        assert NotebookEntry.objects.filter(
            notebook=notebook, meeting_page=page
        ).exists()

    def test_duplicate_returns_already_saved_message(self, client):
        """Test saving same page twice returns already saved."""
        from tests.factories import NotebookEntryFactory

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)
        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "Already in" in response.content.decode()

    def test_can_specify_target_notebook(self, client):
        """Test can save to specific notebook."""
        user = UserFactory()
        _notebook1 = NotebookFactory(user=user, name="Research 1")
        notebook2 = NotebookFactory(user=user, name="Research 2")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id, "notebook_id": str(notebook2.id)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert NotebookEntry.objects.filter(
            notebook=notebook2, meeting_page=page
        ).exists()
