from unittest.mock import Mock, patch

import pytest

from clip.services import FetchError, fetch_single_page
from municipalities.models import Muni


@pytest.fixture
def municipality(db):
    return Muni.objects.create(
        name="Test City",
        subdomain="testcity",
        state="CA",
        country="US",
    )


@pytest.mark.django_db
class TestFetchSinglePage:
    def test_raises_error_for_unknown_municipality(self):
        """Should raise FetchError for unknown subdomain."""
        with pytest.raises(FetchError) as exc_info:
            fetch_single_page("page_id", "unknown", "agendas")

        assert "not found" in str(exc_info.value).lower()

    def test_returns_none_when_page_not_found_in_api(self, municipality):
        """Should return None when page not found in civic.band."""
        with patch("clip.services.httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = Mock(return_value=False)
            mock_response = Mock()
            mock_response.json.return_value = {"rows": []}
            mock_response.raise_for_status = Mock()
            mock_client.get.return_value = mock_response

            result = fetch_single_page("nonexistent_page", "testcity", "agendas")

        assert result is None

    def test_creates_document_and_page_from_api_response(self, municipality):
        """Should create MeetingDocument and MeetingPage from API data."""
        api_response = {
            "rows": [
                {
                    "id": "testcity_agendas_CityCouncil_2024-03-01_1",
                    "meeting": "CityCouncil",
                    "date": "2024-03-01",
                    "page": 1,
                    "text": "Meeting agenda content.",
                    "page_image": "/_agendas/CityCouncil/2024-03-01/1.png",
                }
            ]
        }

        with patch("clip.services.httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = Mock(return_value=False)
            mock_response = Mock()
            mock_response.json.return_value = api_response
            mock_response.raise_for_status = Mock()
            mock_client.get.return_value = mock_response

            page = fetch_single_page(
                "testcity_agendas_CityCouncil_2024-03-01_1",
                "testcity",
                "agendas",
            )

        assert page is not None
        assert page.id == "testcity_agendas_CityCouncil_2024-03-01_1"
        assert page.page_number == 1
        assert page.text == "Meeting agenda content."

        # Verify document was created
        doc = page.document
        assert doc.municipality == municipality
        assert doc.meeting_name == "CityCouncil"
        assert doc.document_type == "agenda"

    def test_raises_fetch_error_on_http_failure(self, municipality):
        """Should raise FetchError on HTTP errors."""
        import httpx

        with patch("clip.services.httpx.Client") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value.__enter__ = Mock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = Mock(return_value=False)
            mock_client.get.side_effect = httpx.HTTPError("Connection failed")

            with pytest.raises(FetchError):
                fetch_single_page("page_id", "testcity", "agendas")
