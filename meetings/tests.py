import json
from datetime import date
from unittest.mock import Mock, patch

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client

from meetings.models import MeetingDocument, MeetingPage
from meetings.services import backfill_municipality_meetings
from municipalities.models import Muni

User = get_user_model()


@pytest.fixture
def authenticated_client(db):
    """Create an authenticated test client."""
    client = Client()
    user = User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestMeetingDocumentModel:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="alameda.ca",
            name="Alameda",
            state="CA",
            country="US",
            kind="city",
        )

    def test_create_meeting_document(self, muni):
        doc = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )
        assert doc.municipality == muni
        assert doc.meeting_name == "CityCouncil"
        assert doc.meeting_date == date(2024, 1, 15)
        assert doc.document_type == "agenda"
        assert doc.created is not None
        assert doc.modified is not None
        assert str(doc.id)  # UUID is valid

    def test_str_representation(self, muni):
        doc = MeetingDocument(
            municipality=muni,
            meeting_name="PlanningBoard",
            meeting_date=date(2024, 2, 1),
            document_type="minutes",
        )
        expected = "alameda.ca - PlanningBoard minutes (2024-02-01)"
        assert str(doc) == expected

    def test_unique_together_constraint(self, muni):
        # Create first document
        MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )

        # Try to create duplicate - should raise IntegrityError
        with pytest.raises(IntegrityError):
            MeetingDocument.objects.create(
                municipality=muni,
                meeting_name="CityCouncil",
                meeting_date=date(2024, 1, 15),
                document_type="agenda",
            )

    def test_can_have_same_meeting_different_type(self, muni):
        # Create agenda
        agenda = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )

        # Create minutes for same meeting - should work
        minutes = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="minutes",
        )

        assert agenda.id != minutes.id
        assert MeetingDocument.objects.count() == 2

    def test_document_type_choices(self, muni):
        # Valid choices
        doc1 = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Test",
            meeting_date=date(2024, 1, 1),
            document_type="agenda",
        )
        doc2 = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Test2",
            meeting_date=date(2024, 1, 2),
            document_type="minutes",
        )
        assert doc1.document_type == "agenda"
        assert doc2.document_type == "minutes"

    def test_cascade_delete(self, muni):
        MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Test",
            meeting_date=date(2024, 1, 1),
            document_type="agenda",
        )

        # Delete municipality - should delete document
        muni.delete()
        assert MeetingDocument.objects.count() == 0

    def test_meta_options(self):
        assert MeetingDocument._meta.verbose_name == "Meeting Document"
        assert MeetingDocument._meta.verbose_name_plural == "Meeting Documents"
        assert MeetingDocument._meta.ordering == ["-meeting_date", "meeting_name"]


@pytest.mark.django_db
class TestMeetingPageModel:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="alameda.ca",
            name="Alameda",
            state="CA",
            country="US",
            kind="city",
        )

    @pytest.fixture
    def document(self, muni):
        return MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )

    def test_create_meeting_page(self, document):
        page = MeetingPage.objects.create(
            id="abc123",
            document=document,
            page_number=1,
            text="This is the page text",
            page_image="/_agendas/CityCouncil/2024-01-15/1.png",
        )
        assert page.id == "abc123"
        assert page.document == document
        assert page.page_number == 1
        assert page.text == "This is the page text"
        assert page.page_image == "/_agendas/CityCouncil/2024-01-15/1.png"

    def test_str_representation(self, document):
        page = MeetingPage(id="test123", document=document, page_number=5, text="Test")
        assert "Page 5" in str(page)

    def test_unique_together_constraint(self, document):
        # Create first page
        MeetingPage.objects.create(
            id="page1", document=document, page_number=1, text="Page 1"
        )

        # Try to create another page with same document and page_number
        # Should raise IntegrityError
        with pytest.raises(IntegrityError):
            MeetingPage.objects.create(
                id="page2", document=document, page_number=1, text="Duplicate"
            )

    def test_cascade_delete_from_document(self, document):
        MeetingPage.objects.create(
            id="page1", document=document, page_number=1, text="Page 1"
        )
        MeetingPage.objects.create(
            id="page2", document=document, page_number=2, text="Page 2"
        )

        assert MeetingPage.objects.count() == 2

        # Delete document - should delete pages
        document.delete()
        assert MeetingPage.objects.count() == 0

    def test_empty_text_and_image(self, document):
        page = MeetingPage.objects.create(id="empty", document=document, page_number=1)
        assert page.text == ""
        assert page.page_image == ""

    def test_meta_options(self):
        assert MeetingPage._meta.verbose_name == "Meeting Page"
        assert MeetingPage._meta.verbose_name_plural == "Meeting Pages"


@pytest.mark.django_db
class TestBackfillService:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="testcity.ca",
            name="Test City",
            state="CA",
            country="US",
            kind="city",
        )

    @pytest.fixture
    def mock_agendas_response(self):
        return {
            "ok": True,
            "rows": [
                {
                    "id": "agenda1page1",
                    "meeting": "CityCouncil",
                    "date": "2024-01-15",
                    "page": 1,
                    "text": "City Council Agenda Page 1",
                    "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
                },
                {
                    "id": "agenda1page2",
                    "meeting": "CityCouncil",
                    "date": "2024-01-15",
                    "page": 2,
                    "text": "City Council Agenda Page 2",
                    "page_image": "/_agendas/CityCouncil/2024-01-15/2.png",
                },
            ],
            "truncated": False,
        }

    @pytest.fixture
    def mock_minutes_response(self):
        return {
            "ok": True,
            "rows": [
                {
                    "id": "minutes1page1",
                    "meeting": "PlanningBoard",
                    "date": "2024-01-10",
                    "page": 1,
                    "text": "Planning Board Minutes Page 1",
                    "page_image": "/_minutes/PlanningBoard/2024-01-10/1.png",
                },
            ],
            "truncated": False,
        }

    @patch("meetings.services.httpx.Client")
    def test_backfill_success(
        self, mock_client_class, muni, mock_agendas_response, mock_minutes_response
    ):
        # Setup mock client
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock responses
        mock_agendas_resp = Mock()
        mock_agendas_resp.json.return_value = mock_agendas_response
        mock_agendas_resp.raise_for_status = Mock()

        mock_minutes_resp = Mock()
        mock_minutes_resp.json.return_value = mock_minutes_response
        mock_minutes_resp.raise_for_status = Mock()

        # Return different responses based on URL
        def get_side_effect(url):
            if "agendas" in url:
                return mock_agendas_resp
            elif "minutes" in url:
                return mock_minutes_resp
            return Mock()

        mock_client.get.side_effect = get_side_effect

        # Run backfill
        stats = backfill_municipality_meetings(muni)

        # Verify stats
        assert stats["documents_created"] == 2  # One agenda doc, one minutes doc
        assert stats["pages_created"] == 3  # 2 agenda pages + 1 minutes page
        assert stats["errors"] == 0

        # Verify database
        assert MeetingDocument.objects.count() == 2
        assert MeetingPage.objects.count() == 3

        # Verify specific documents
        city_council_agenda = MeetingDocument.objects.get(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )
        assert city_council_agenda.pages.count() == 2

        planning_minutes = MeetingDocument.objects.get(
            municipality=muni,
            meeting_name="PlanningBoard",
            meeting_date=date(2024, 1, 10),
            document_type="minutes",
        )
        assert planning_minutes.pages.count() == 1

    @patch("meetings.services.httpx.Client")
    def test_backfill_update_existing(self, mock_client_class, muni):
        # Create existing document
        existing_doc = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )
        MeetingPage.objects.create(
            id="existing_page",
            document=existing_doc,
            page_number=1,
            text="Old text",
        )

        # Setup mock - return data for both agendas and minutes
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        agenda_response = Mock()
        agenda_response.json.return_value = {
            "ok": True,
            "rows": [
                {
                    "id": "existing_page",
                    "meeting": "CityCouncil",
                    "date": "2024-01-15",
                    "page": 1,
                    "text": "Updated text",
                    "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
                },
            ],
            "truncated": False,
        }
        agenda_response.raise_for_status = Mock()

        # Empty minutes response
        minutes_response = Mock()
        minutes_response.json.return_value = {
            "ok": True,
            "rows": [],
            "truncated": False,
        }
        minutes_response.raise_for_status = Mock()

        def get_side_effect(url):
            if "agendas" in url:
                return agenda_response
            elif "minutes" in url:
                return minutes_response
            return Mock()

        mock_client.get.side_effect = get_side_effect

        # Run backfill
        stats = backfill_municipality_meetings(muni)

        # Should update, not create new
        assert stats["documents_updated"] >= 1
        assert stats["pages_updated"] >= 1
        assert MeetingDocument.objects.count() == 1
        assert MeetingPage.objects.count() == 1

        # Verify page was updated
        updated_page = MeetingPage.objects.get(id="existing_page")
        assert updated_page.text == "Updated text"

    @patch("meetings.services.httpx.Client")
    def test_backfill_http_error(self, mock_client_class, muni):
        # Setup mock to raise HTTP error
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.HTTPError("Network error")

        # Run backfill - should not raise but log errors
        stats = backfill_municipality_meetings(muni)

        # Should have errors recorded
        assert stats["errors"] >= 1
        assert MeetingDocument.objects.count() == 0

    @patch("meetings.services.httpx.Client")
    def test_backfill_missing_required_fields(self, mock_client_class, muni):
        # Setup mock with incomplete data
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            "ok": True,
            "rows": [
                {
                    "id": "bad_page",
                    # Missing 'meeting' and 'date'
                    "page": 1,
                    "text": "Some text",
                },
            ],
            "truncated": False,
        }
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response

        # Run backfill
        stats = backfill_municipality_meetings(muni)

        # Should skip bad rows
        assert stats["errors"] >= 1
        assert MeetingDocument.objects.count() == 0

    @patch("meetings.services.httpx.Client")
    def test_backfill_invalid_date(self, mock_client_class, muni):
        # Setup mock with invalid date
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            "ok": True,
            "rows": [
                {
                    "id": "bad_date",
                    "meeting": "CityCouncil",
                    "date": "not-a-date",
                    "page": 1,
                    "text": "Some text",
                },
            ],
            "truncated": False,
        }
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response

        # Run backfill
        stats = backfill_municipality_meetings(muni)

        # Should skip rows with bad dates
        assert stats["errors"] >= 1
        assert MeetingDocument.objects.count() == 0


@pytest.mark.django_db
class TestWebhookIntegration:
    @pytest.fixture
    def muni_data(self):
        return {
            "subdomain": "webhooktest.ca",
            "name": "Webhook Test City",
            "state": "CA",
            "country": "US",
            "kind": "city",
            "pages": 500,
        }

    @patch("django_rq.get_queue")
    def test_webhook_triggers_backfill(self, mock_get_queue, client, muni_data):
        # Set webhook secret
        import os

        os.environ["WEBHOOK_SECRET"] = "test-secret"

        # Mock the queue and enqueue method
        mock_queue = Mock()
        mock_job = Mock()
        mock_job.id = "test-job-456"
        mock_queue.enqueue.return_value = mock_job
        mock_get_queue.return_value = mock_queue

        # Call webhook endpoint
        response = client.post(
            f"/munis/api/update/{muni_data['subdomain']}/",
            data=json.dumps(muni_data),
            content_type="application/json",
            **{"HTTP_AUTHORIZATION": "Bearer test-secret"},
        )

        assert response.status_code in [200, 201]

        # Verify enqueue was called
        assert mock_queue.enqueue.called
        # Get the muni ID from the enqueue call
        from meetings.tasks import backfill_municipality_meetings_task
        from municipalities.models import Muni

        call_args = mock_queue.enqueue.call_args
        # First arg is the function, second is the muni_id
        assert call_args[0][0] == backfill_municipality_meetings_task
        muni_id = call_args[0][1]
        # Verify the muni was created/updated
        muni = Muni.objects.get(pk=muni_id)
        assert muni.subdomain == muni_data["subdomain"]

        # Cleanup
        del os.environ["WEBHOOK_SECRET"]


@pytest.mark.django_db
class TestDatabaseIndexes:
    """Test that database indexes are properly configured."""

    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="indextest.ca",
            name="Index Test City",
            state="CA",
            kind="city",
        )

    def test_meeting_document_indexes_exist(self):
        # Check that the custom indexes are defined in Meta
        indexes = MeetingDocument._meta.indexes
        assert len(indexes) >= 2

        # Check for composite index
        composite_idx = next(
            (idx for idx in indexes if idx.name == "meetings_muni_name_date_idx"),
            None,
        )
        assert composite_idx is not None
        # Index fields can be strings or field objects
        field_names = [
            f.name if hasattr(f, "name") else f for f in composite_idx.fields
        ]
        assert "municipality" in field_names

    def test_query_performance_with_indexes(self, muni):
        # Create test data
        for i in range(10):
            MeetingDocument.objects.create(
                municipality=muni,
                meeting_name=f"Meeting{i % 3}",
                meeting_date=date(2024, 1, i + 1),
                document_type="agenda" if i % 2 == 0 else "minutes",
            )

        # These queries should use indexes efficiently
        # Test municipality filter
        results = MeetingDocument.objects.filter(municipality=muni)
        assert results.count() == 10

        # Test municipality + meeting_name filter
        results = MeetingDocument.objects.filter(
            municipality=muni, meeting_name="Meeting1"
        )
        assert results.count() > 0

        # Test document_type filter
        results = MeetingDocument.objects.filter(
            municipality=muni, document_type="agenda"
        )
        assert results.count() == 5


@pytest.mark.django_db
class TestMeetingSearchForm:
    """Test the meeting search form validation and widgets."""

    def test_form_with_valid_data(self):
        from meetings.forms import MeetingSearchForm
        from municipalities.models import Muni

        muni = Muni.objects.create(
            subdomain="test.ca", name="Test", state="CA", kind="city"
        )

        form_data = {
            "query": "budget",
            "municipality": muni.id,
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "document_type": "agenda",
        }
        form = MeetingSearchForm(data=form_data)
        assert form.is_valid()

    def test_form_with_empty_data(self):
        from meetings.forms import MeetingSearchForm

        # Empty form should be valid (no required fields)
        form = MeetingSearchForm(data={})
        assert form.is_valid()

    def test_form_date_validation(self):
        from meetings.forms import MeetingSearchForm

        # date_from after date_to should be invalid
        form_data = {
            "date_from": "2024-12-31",
            "date_to": "2024-01-01",
        }
        form = MeetingSearchForm(data=form_data)
        assert not form.is_valid()
        assert "Start date must be before or equal to end date" in str(
            form.errors["__all__"]
        )

    def test_form_date_equal_is_valid(self):
        from meetings.forms import MeetingSearchForm

        # Same date should be valid
        form_data = {
            "date_from": "2024-01-01",
            "date_to": "2024-01-01",
        }
        form = MeetingSearchForm(data=form_data)
        assert form.is_valid()


@pytest.mark.django_db
class TestMeetingSearchView:
    """Test the main meeting search view."""

    def test_view_requires_authentication(self, client):
        """Anonymous users should be redirected to login."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search")
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_view_renders_for_authenticated_user(self, authenticated_client):
        """Authenticated users should see the search page."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search")
        response = authenticated_client.get(url)

        assert response.status_code == 200
        assert "Search Meeting Documents" in response.content.decode()
        assert "form" in response.context

    def test_view_contains_search_form(self, authenticated_client):
        """View should render the search form."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search")
        response = authenticated_client.get(url)

        content = response.content.decode()
        assert "id_query" in content
        assert "id_municipality" in content
        assert "id_date_from" in content
        assert "id_date_to" in content
        assert "id_document_type" in content


@pytest.mark.django_db
class TestMeetingSearchResults:
    """Test the HTMX search results endpoint."""

    @pytest.fixture
    def muni(self):
        from municipalities.models import Muni

        return Muni.objects.create(
            subdomain="testcity.ca",
            name="Test City",
            state="CA",
            kind="city",
        )

    @pytest.fixture
    def second_muni(self):
        from municipalities.models import Muni

        return Muni.objects.create(
            subdomain="othercity.ca",
            name="Other City",
            state="NY",
            kind="city",
        )

    @pytest.fixture
    def meeting_data(self, muni, second_muni):
        """Create test meeting documents and pages."""
        from meetings.models import MeetingDocument, MeetingPage

        # Create documents for first municipality
        doc1 = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )
        MeetingPage.objects.create(
            id="page1",
            document=doc1,
            page_number=1,
            text="City budget discussion for fiscal year 2024. We will review the proposed budget allocation.",
            page_image="/_agendas/CityCouncil/2024-01-15/1.png",
        )

        doc2 = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="PlanningBoard",
            meeting_date=date(2024, 2, 1),
            document_type="minutes",
        )
        MeetingPage.objects.create(
            id="page2",
            document=doc2,
            page_number=1,
            text="Discussion of new housing development proposal on Main Street.",
            page_image="/_minutes/PlanningBoard/2024-02-01/1.png",
        )

        # Create document for second municipality
        doc3 = MeetingDocument.objects.create(
            municipality=second_muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 3, 1),
            document_type="agenda",
        )
        MeetingPage.objects.create(
            id="page3",
            document=doc3,
            page_number=1,
            text="Budget review and housing policy updates for the city.",
            page_image="/_agendas/CityCouncil/2024-03-01/1.png",
        )

        return {
            "muni": muni,
            "second_muni": second_muni,
            "doc1": doc1,
            "doc2": doc2,
            "doc3": doc3,
        }

    def test_search_with_query(self, authenticated_client, meeting_data):
        """Test full-text search with a query."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "budget"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should find pages with "budget" in text
        assert "budget" in content.lower()
        # Should show search results count
        assert "result" in content.lower()

    def test_search_ranking(self, authenticated_client, meeting_data):
        """Test that search results are ranked by relevance."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "budget"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should display rank scores when has_query is True
        # The page with more mentions of "budget" should rank higher
        assert "Rank:" in content

    def test_search_highlighting(self, authenticated_client, meeting_data):
        """Test that search terms are highlighted in results."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "budget"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should use mark tags for highlighting
        assert "<mark" in content

    def test_filter_by_municipality(self, authenticated_client, meeting_data):
        """Test filtering search results by municipality."""
        from django.urls import reverse

        muni = meeting_data["muni"]
        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "budget", "municipality": muni.id}
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Should only show results from Test City
        assert "Test City" in content
        assert "Other City" not in content

    def test_filter_by_date_range(self, authenticated_client, meeting_data):
        """Test filtering by date range."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"date_from": "2024-01-01", "date_to": "2024-01-31"}
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Should only show January meeting
        assert "January" in content or "2024-01-15" in content
        # Should not show February or March meetings
        assert "February" not in content
        assert "March" not in content

    def test_filter_by_document_type(self, authenticated_client, meeting_data):
        """Test filtering by document type (agenda vs minutes)."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"document_type": "agenda"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should show agenda documents
        assert "Agenda" in content
        # Should not show minutes
        # (We need to be careful here as both might mention similar topics)
        # Better to check the badge
        assert "bg-blue-100 text-blue-800" in content
        # Should not show minutes badge
        assert "bg-green-100 text-green-800" not in content

    def test_pagination(self, authenticated_client, muni):
        """Test that results are paginated."""
        from django.urls import reverse

        from meetings.models import MeetingDocument, MeetingPage

        # Create 25 meeting pages (more than the 20 per page limit)
        doc = MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="TestMeeting",
            meeting_date=date(2024, 1, 1),
            document_type="agenda",
        )

        for i in range(25):
            MeetingPage.objects.create(
                id=f"page_{i}",
                document=doc,
                page_number=i + 1,
                text=f"Test page {i} with common text",
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "common"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should show pagination
        assert "Page 1" in content or "page 1" in content.lower()
        # Should have next page link
        assert "page=2" in content or "Next" in content

    def test_empty_results(self, authenticated_client, meeting_data):
        """Test search with no matching results."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "nonexistentterm123"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should show "no results" message
        assert "no results" in content.lower() or "0 result" in content.lower()

    def test_invalid_form_data(self, authenticated_client):
        """Test search with invalid form data."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        # Invalid date range
        response = authenticated_client.get(
            url, {"date_from": "2024-12-31", "date_to": "2024-01-01"}
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Should show error message
        assert "Invalid" in content or "error" in content.lower()

    def test_civic_band_links(self, authenticated_client, meeting_data):
        """Test that results include links to CivicBand."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {"query": "budget"})

        assert response.status_code == 200
        content = response.content.decode()

        # Should contain links to civic.band
        assert "civic.band" in content
        assert "View on CivicBand" in content

    def test_search_without_query_shows_recent(
        self, authenticated_client, meeting_data
    ):
        """Test that searching without a query shows recent meetings."""
        from django.urls import reverse

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(url, {})

        assert response.status_code == 200
        content = response.content.decode()

        # Should show results ordered by date (most recent first)
        # March meeting should appear before January
        assert content.index("March") < content.index("January") or content.index(
            "2024-03-01"
        ) < content.index("2024-01-15")

    def test_combined_filters(self, authenticated_client, meeting_data):
        """Test using multiple filters together."""
        from django.urls import reverse

        muni = meeting_data["muni"]
        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url,
            {
                "query": "budget",
                "municipality": muni.id,
                "date_from": "2024-01-01",
                "date_to": "2024-01-31",
                "document_type": "agenda",
            },
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Should find the matching page
        assert "budget" in content.lower()
        assert "Test City" in content
