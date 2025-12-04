from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from meetings.models import MeetingDocument, MeetingPage
from municipalities.models import Muni


@pytest.fixture
def municipality(db):
    return Muni.objects.create(
        name="Test City",
        subdomain="testcity",
        state="CA",
        country="US",
    )


@pytest.fixture
def meeting_document(municipality):
    return MeetingDocument.objects.create(
        municipality=municipality,
        meeting_name="CityCouncil",
        meeting_date="2024-01-15",
        document_type="agenda",
    )


@pytest.fixture
def meeting_page(meeting_document):
    return MeetingPage.objects.create(
        id="testcity_agendas_CityCouncil_2024-01-15_1",
        document=meeting_document,
        page_number=1,
        text="This is the first page of the city council agenda discussing budget items and zoning changes.",
    )


@pytest.mark.django_db
class TestFetchPageView:
    def test_fetch_page_requires_auth(self, client):
        """Unauthenticated users should get 302 redirect."""
        url = reverse("clip:fetch-page")
        response = client.get(
            url, {"id": "test", "subdomain": "test", "table": "agendas"}
        )
        assert response.status_code == 302

    def test_fetch_page_returns_preview_for_existing_page(
        self, client, django_user_model, meeting_page
    ):
        """Should return preview HTML for existing meeting page."""
        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        url = reverse("clip:fetch-page")
        response = client.get(
            url,
            {
                "id": meeting_page.id,
                "subdomain": "testcity",
                "table": "agendas",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert b"CityCouncil" in response.content
        assert b"2024-01-15" in response.content or b"Jan" in response.content


@pytest.mark.django_db
class TestFetchPageViewRemote:
    def test_fetch_page_fetches_from_civic_band_when_not_local(
        self, client, django_user_model, municipality
    ):
        """Should fetch from civic.band API when page not found locally."""
        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        # Mock the civic.band API response
        mock_response_data = {
            "rows": [
                {
                    "id": "testcity_agendas_CityCouncil_2024-02-01_1",
                    "meeting": "CityCouncil",
                    "date": "2024-02-01",
                    "page": 1,
                    "text": "Budget discussion for Q1 2024.",
                    "page_image": "/_agendas/CityCouncil/2024-02-01/1.png",
                }
            ]
        }

        with patch("clip.services.httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = Mock(return_value=False)
            mock_response = Mock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = Mock()
            mock_client.get.return_value = mock_response

            url = reverse("clip:fetch-page")
            response = client.get(
                url,
                {
                    "id": "testcity_agendas_CityCouncil_2024-02-01_1",
                    "subdomain": "testcity",
                    "table": "agendas",
                },
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 200
        assert b"CityCouncil" in response.content

        # Verify page was created locally
        assert MeetingPage.objects.filter(
            id="testcity_agendas_CityCouncil_2024-02-01_1"
        ).exists()


@pytest.mark.django_db
class TestSavePageView:
    def test_save_page_requires_auth(self, client):
        """Unauthenticated users should get 302 redirect."""
        url = reverse("clip:save-page")
        response = client.post(url, {"page_id": "test"})
        assert response.status_code == 302

    def test_save_page_creates_entry_and_redirects(
        self, client, django_user_model, meeting_page
    ):
        """Should create notebook entry and redirect to notebook."""
        from notebooks.models import Notebook, NotebookEntry

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        notebook = Notebook.objects.create(user=user, name="Test Notebook")
        client.force_login(user)

        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "notebook_id": str(notebook.id),
                "note": "Important budget info",
            },
        )

        # Should redirect to notebook detail
        assert response.status_code == 302
        assert str(notebook.id) in response.url

        # Verify entry was created
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=meeting_page)
        assert entry.note == "Important budget info"

    def test_save_page_creates_new_notebook_if_specified(
        self, client, django_user_model, meeting_page
    ):
        """Should create new notebook if new_notebook_name is provided."""
        from notebooks.models import Notebook, NotebookEntry

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "new_notebook_name": "My New Research",
                "note": "Starting research on this topic",
            },
        )

        assert response.status_code == 302

        # Verify notebook was created
        notebook = Notebook.objects.get(user=user, name="My New Research")
        assert notebook is not None

        # Verify entry was created
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=meeting_page)
        assert entry.note == "Starting research on this topic"

    def test_save_page_with_existing_tags(
        self, client, django_user_model, meeting_page
    ):
        """Should add existing tags to the entry."""
        from notebooks.models import Notebook, NotebookEntry, Tag

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        notebook = Notebook.objects.create(user=user, name="Test Notebook")
        tag1 = Tag.objects.create(user=user, name="budget")
        tag2 = Tag.objects.create(user=user, name="zoning")
        client.force_login(user)

        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "notebook_id": str(notebook.id),
                "tags": [str(tag1.id), str(tag2.id)],
                "note": "Budget and zoning discussion",
            },
        )

        assert response.status_code == 302

        # Verify entry was created with tags
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=meeting_page)
        assert entry.tags.count() == 2
        assert tag1 in entry.tags.all()
        assert tag2 in entry.tags.all()

    def test_save_page_with_new_tag(self, client, django_user_model, meeting_page):
        """Should create and add a new tag when new_tag parameter is provided."""
        from notebooks.models import Notebook, NotebookEntry, Tag

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        notebook = Notebook.objects.create(user=user, name="Test Notebook")
        client.force_login(user)

        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "notebook_id": str(notebook.id),
                "new_tag": "Infrastructure",
                "note": "Infrastructure planning",
            },
        )

        assert response.status_code == 302

        # Verify tag was created (lowercased)
        tag = Tag.objects.get(user=user, name="infrastructure")
        assert tag is not None

        # Verify entry was created with the new tag
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=meeting_page)
        assert tag in entry.tags.all()

    def test_save_page_already_saved_redirects(
        self, client, django_user_model, meeting_page
    ):
        """Should redirect without creating duplicate when page already saved to notebook."""
        from notebooks.models import Notebook, NotebookEntry

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        notebook = Notebook.objects.create(user=user, name="Test Notebook")
        client.force_login(user)

        # Create existing entry
        existing_entry = NotebookEntry.objects.create(
            notebook=notebook,
            meeting_page=meeting_page,
            note="Original note",
        )

        # Try to save the same page again
        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "notebook_id": str(notebook.id),
                "note": "This should not be saved",
            },
        )

        # Should redirect to notebook
        assert response.status_code == 302
        assert str(notebook.id) in response.url

        # Should still have only one entry
        assert (
            NotebookEntry.objects.filter(
                notebook=notebook, meeting_page=meeting_page
            ).count()
            == 1
        )

        # Original entry should be unchanged
        existing_entry.refresh_from_db()
        assert existing_entry.note == "Original note"

    def test_save_page_creates_default_notebook_if_none_exist(
        self, client, django_user_model, meeting_page
    ):
        """Should create default 'My Notebook' when no notebook_id provided and none exist."""
        from notebooks.models import Notebook, NotebookEntry

        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        # Verify user has no notebooks
        assert Notebook.objects.filter(user=user).count() == 0

        url = reverse("clip:save-page")
        response = client.post(
            url,
            {
                "page_id": meeting_page.id,
                "note": "Saved to auto-created notebook",
            },
        )

        assert response.status_code == 302

        # Verify default notebook was created
        notebook = Notebook.objects.get(user=user, name="My Notebook")
        assert notebook is not None
        assert str(notebook.id) in response.url

        # Verify entry was created in the default notebook
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=meeting_page)
        assert entry.note == "Saved to auto-created notebook"
