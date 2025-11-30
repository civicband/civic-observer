import pytest
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    NotebookEntryFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchResultsSaveButton:
    def test_save_button_appears_for_authenticated_users(self, client):
        """Test save button appears in search results for logged-in users."""
        user = UserFactory()
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy discussion")

        client.force_login(user)
        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        # Button now uses hx-get to open the save panel
        assert "hx-get" in response.content.decode()
        assert "save-panel" in response.content.decode()

    def test_save_button_shows_saved_state_for_already_saved(self, client):
        """Test save button shows filled state when page already saved."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing policy discussion")
        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        client.force_login(user)
        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        # Check for filled bookmark icon (saved state)
        assert 'fill="currentColor"' in response.content.decode()

    def test_no_save_button_for_anonymous_users(self, client):
        """Test save button not shown for anonymous users."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy discussion")

        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert "save-page" not in response.content.decode()
