from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from meetings.models import MeetingDocument, MeetingPage
from municipalities.models import Muni
from research.models import Code, Coding, Memo, SetItem
from tests.factories import (
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


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

    def test_save_page_adds_to_research_set(self, client, meeting_page):
        """Should add the page to the selected research set and redirect."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, name="Saved pages")
        client.force_login(user)

        response = client.post(
            reverse("clip:save-page"),
            {
                "page_id": meeting_page.id,
                "set": str(result_set.id),
                "note": "Important budget info",
            },
        )

        assert response.status_code == 302
        assert str(result_set.id) in response.url
        assert SetItem.objects.filter(result_set=result_set, page=meeting_page).exists()
        assert Memo.objects.filter(
            project=project, page=meeting_page, body="Important budget info"
        ).exists()

    def test_save_page_uses_default_set_when_none_given(self, client, meeting_page):
        """Should create the default set when no set is selected."""
        user = UserFactory()
        client.force_login(user)

        response = client.post(
            reverse("clip:save-page"),
            {"page_id": meeting_page.id, "note": "Saved to auto-created set"},
        )

        assert response.status_code == 302
        item = SetItem.objects.get(page=meeting_page)
        assert item.result_set.name == "Saved pages"
        assert item.result_set.project.name == "My research"
        assert str(item.result_set.id) in response.url

    def test_save_page_applies_codes(self, client, meeting_page):
        """Should create the named code and code the page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        client.force_login(user)

        response = client.post(
            reverse("clip:save-page"),
            {
                "page_id": meeting_page.id,
                "set": str(result_set.id),
                "codes": ["Housing"],
            },
        )

        assert response.status_code == 302
        code = Code.objects.get(project=project, name="Housing")
        assert Coding.objects.filter(code=code, page=meeting_page).exists()

    def test_save_page_already_saved_does_not_duplicate(self, client, meeting_page):
        """Saving the same page twice keeps one SetItem."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=meeting_page)
        client.force_login(user)

        response = client.post(
            reverse("clip:save-page"),
            {"page_id": meeting_page.id, "set": str(result_set.id)},
        )

        assert response.status_code == 302
        assert (
            SetItem.objects.filter(result_set=result_set, page=meeting_page).count()
            == 1
        )

    def test_cannot_save_to_another_users_set(self, client, meeting_page):
        """A user cannot add a page to someone else's set."""
        user = UserFactory()
        other_set = ResultSetFactory(project=ProjectFactory())
        client.force_login(user)

        response = client.post(
            reverse("clip:save-page"),
            {"page_id": meeting_page.id, "set": str(other_set.id)},
        )

        assert response.status_code == 404
        assert not SetItem.objects.filter(page=meeting_page).exists()


@pytest.mark.django_db
class TestClipIntegration:
    def test_full_clip_flow_unauthenticated(self, client):
        """Unauthenticated user should be redirected to login."""
        url = reverse("clip:clip")
        response = client.get(
            url,
            {"id": "test_page", "subdomain": "testcity", "table": "agendas"},
        )

        # Should redirect to login with next param preserved
        assert response.status_code == 302
        assert "/login/" in response.url
        assert "next=" in response.url

    def test_full_clip_flow_authenticated_with_existing_page(
        self, client, django_user_model, meeting_page
    ):
        """Authenticated user with existing page should see preview."""
        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        # First request - main clip page
        url = reverse("clip:clip")
        response = client.get(
            url,
            {
                "id": meeting_page.id,
                "subdomain": "testcity",
                "table": "agendas",
            },
        )

        assert response.status_code == 200
        assert b"Add to Research Set" in response.content
        # Should have HTMX trigger for fetch
        assert b"hx-get" in response.content

    def test_clip_page_without_params_shows_error(self, client, django_user_model):
        """Clip page without params should show error message."""
        user = django_user_model.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        client.force_login(user)

        url = reverse("clip:clip")
        response = client.get(url)

        assert response.status_code == 200
        assert b"Missing parameters" in response.content
