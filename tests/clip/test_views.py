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
