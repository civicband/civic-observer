"""
Integration tests for the saved search system.

These tests verify the complete end-to-end workflow:
1. Creating searches with all filter types
2. Saving searches from parameters
3. Triggering notifications after new pages are ingested
4. Sending digest emails

Since pg_search operators aren't available in the test database, we mock
the search backend to return results based on the pages created in each test.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from meetings.models import MeetingPage
from searches.models import SavedSearch, Search
from searches.tasks import (
    check_all_immediate_searches,
    check_saved_search_for_updates,
    send_daily_digests,
    send_weekly_digests,
)
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    UserFactory,
)

User = get_user_model()


def _mock_backend_returning_all_pages():
    """Create a mock backend that returns all MeetingPage IDs."""
    from unittest.mock import MagicMock

    mock_backend = MagicMock()

    def search_side_effect(**kwargs):
        # Return all pages in the database matching the basic criteria
        qs = MeetingPage.objects.all()
        query_text = kwargs.get("query_text", "")
        municipalities = kwargs.get("municipalities")
        states = kwargs.get("states")
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        document_type = kwargs.get("document_type")

        if query_text:
            qs = qs.filter(text__icontains=query_text)
        if municipalities is not None:
            if hasattr(municipalities, "values_list"):
                muni_ids = list(municipalities.values_list("id", flat=True))
            else:
                muni_ids = [m.id if hasattr(m, "id") else m for m in municipalities]
            if muni_ids:
                qs = qs.filter(municipality_id__in=muni_ids)
        if states:
            qs = qs.filter(state__in=states)
        if date_from:
            qs = qs.filter(meeting_date__gte=date_from)
        if date_to:
            qs = qs.filter(meeting_date__lte=date_to)
        if document_type and document_type != "all":
            qs = qs.filter(document_type=document_type)

        results = [{"id": p.id} for p in qs]
        return results, len(results)

    mock_backend.search.side_effect = search_side_effect
    return mock_backend


@pytest.mark.django_db
class TestEndToEndWorkflow:
    """Test complete workflow from search creation to notification."""

    @patch("searches.search_backends.get_search_backend")
    def test_complete_immediate_notification_workflow(self, mock_get_backend):
        """
        Test the complete workflow:
        1. Create a search with multiple filters
        2. Create a saved search with immediate notifications
        3. Ingest new pages that match
        4. Verify notification is sent
        """
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        user = UserFactory(email="test@example.com")
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")

        search = Search.objects.get_or_create_for_params(
            search_term="housing",
            municipalities=[muni1, muni2],
            states=["CA"],
            document_type="agenda",
        )

        saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Bay Area Housing Updates",
            notification_frequency="immediate",
        )

        assert len(mail.outbox) == 0

        doc = MeetingDocumentFactory(
            municipality=muni1, document_type="agenda", meeting_date="2025-01-15"
        )
        _page = MeetingPageFactory(
            document=doc, text="Discussion about affordable housing programs in Oakland"
        )

        check_saved_search_for_updates(saved_search.id)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["test@example.com"]
        assert "housing" in email.body.lower()
        assert "Bay Area Housing Updates" in email.body

        saved_search.refresh_from_db()
        assert saved_search.last_notification_sent is not None
        assert saved_search.has_pending_results is False

    @patch("searches.search_backends.get_search_backend")
    def test_complete_daily_digest_workflow(self, mock_get_backend):
        """
        Test daily digest workflow:
        1. Create multiple saved searches with daily digest
        2. Ingest pages that match
        3. Verify has_pending_results is set
        4. Run daily digest task
        5. Verify combined email is sent
        """
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        user = UserFactory(email="digest@example.com")
        muni = MuniFactory(name="San Francisco", state="CA")

        search1 = Search.objects.get_or_create_for_params(
            search_term="budget", municipalities=[muni]
        )
        search2 = Search.objects.get_or_create_for_params(
            search_term="zoning", municipalities=[muni]
        )

        saved_search1 = SavedSearch.objects.create(
            user=user,
            search=search1,
            name="Budget Watch",
            notification_frequency="daily",
        )
        saved_search2 = SavedSearch.objects.create(
            user=user,
            search=search2,
            name="Zoning Changes",
            notification_frequency="daily",
        )

        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="Budget discussion for fiscal year 2025")
        MeetingPageFactory(document=doc, text="Proposed zoning changes for downtown")

        check_saved_search_for_updates(saved_search1.id)
        check_saved_search_for_updates(saved_search2.id)

        assert len(mail.outbox) == 0

        saved_search1.refresh_from_db()
        saved_search2.refresh_from_db()
        assert saved_search1.has_pending_results is True
        assert saved_search2.has_pending_results is True

        send_daily_digests()

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["digest@example.com"]
        assert "Budget Watch" in email.body
        assert "Zoning Changes" in email.body
        assert "daily" in email.subject.lower()

        saved_search1.refresh_from_db()
        saved_search2.refresh_from_db()
        assert saved_search1.has_pending_results is False
        assert saved_search2.has_pending_results is False

    @patch("searches.search_backends.get_search_backend")
    def test_batch_notification_after_ingest(self, mock_get_backend):
        """
        Test that after ingesting multiple pages, all immediate searches are checked.
        """
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        user1 = UserFactory(email="user1@example.com")
        user2 = UserFactory(email="user2@example.com")
        muni = MuniFactory(name="Portland", state="OR")

        search1 = Search.objects.get_or_create_for_params(
            search_term="transportation", municipalities=[muni]
        )
        search2 = Search.objects.get_or_create_for_params(
            search_term="",
            municipalities=[muni],
        )

        SavedSearch.objects.create(
            user=user1,
            search=search1,
            name="Transportation Updates",
            notification_frequency="immediate",
        )
        SavedSearch.objects.create(
            user=user2,
            search=search2,
            name="All Portland Updates",
            notification_frequency="immediate",
        )

        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(
            document=doc,
            text="New light rail transportation project proposal for downtown",
        )

        check_all_immediate_searches()

        assert len(mail.outbox) == 2
        recipients = {email.to[0] for email in mail.outbox}
        assert recipients == {"user1@example.com", "user2@example.com"}


@pytest.mark.django_db
class TestSearchFilterCombinations:
    """Test various search filter combinations work correctly."""

    def test_search_with_all_filters(self):
        """Test search with every filter type set."""
        muni1 = MuniFactory(name="Seattle", state="WA")
        muni2 = MuniFactory(name="Tacoma", state="WA")

        search = Search.objects.get_or_create_for_params(
            search_term="climate",
            municipalities=[muni1, muni2],
            states=["WA", "OR"],
            date_from="2025-01-01",
            date_to="2025-12-31",
            document_type="minutes",
            meeting_name_query="planning OR council",
        )

        assert search.search_term == "climate"
        assert search.municipalities.count() == 2
        assert search.states == ["WA", "OR"]
        assert search.document_type == "minutes"
        assert search.meeting_name_query == "planning OR council"

    @patch("searches.search_backends.get_search_backend")
    def test_all_updates_mode_with_multiple_municipalities(self, mock_get_backend):
        """Test all updates mode (empty search_term) with multiple municipalities."""
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        user = UserFactory()
        muni1 = MuniFactory(name="Austin", state="TX")
        muni2 = MuniFactory(name="Dallas", state="TX")

        search = Search.objects.get_or_create_for_params(
            search_term="",
            municipalities=[muni1, muni2],
        )

        _saved_search = SavedSearch.objects.create(
            user=user, search=search, name="All Texas Updates"
        )

        doc1 = MeetingDocumentFactory(municipality=muni1)
        doc2 = MeetingDocumentFactory(municipality=muni2)
        page1 = MeetingPageFactory(document=doc1, text="Austin city council agenda")
        page2 = MeetingPageFactory(document=doc2, text="Dallas planning meeting")

        new_pages = search.update_search()
        assert new_pages.count() == 2
        assert page1 in new_pages
        assert page2 in new_pages

    @patch("searches.search_backends.get_search_backend")
    def test_date_range_filtering(self, mock_get_backend):
        """Test that date range filters work correctly."""
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        muni = MuniFactory(name="Boston", state="MA")

        search = Search.objects.get_or_create_for_params(
            search_term="budget",
            municipalities=[muni],
            date_from="2025-02-01",
            date_to="2025-02-28",
        )

        doc_in_range = MeetingDocumentFactory(
            municipality=muni, meeting_date="2025-02-15"
        )
        doc_before = MeetingDocumentFactory(
            municipality=muni, meeting_date="2025-01-15"
        )
        doc_after = MeetingDocumentFactory(municipality=muni, meeting_date="2025-03-15")

        page_in_range = MeetingPageFactory(
            document=doc_in_range, text="Budget proposal for FY2025"
        )
        MeetingPageFactory(document=doc_before, text="Budget from January")
        MeetingPageFactory(document=doc_after, text="Budget from March")

        new_pages = search.update_search()
        assert new_pages.count() == 1
        assert page_in_range in new_pages


@pytest.mark.django_db
class TestNotificationPreferences:
    """Test different notification frequency preferences."""

    @patch("searches.search_backends.get_search_backend")
    def test_switching_notification_frequency(self, mock_get_backend):
        """Test that changing notification frequency works correctly."""
        mock_get_backend.return_value = _mock_backend_returning_all_pages()

        user = UserFactory()
        muni = MuniFactory()

        doc = MeetingDocumentFactory(municipality=muni)
        _page1 = MeetingPageFactory(document=doc, text="New parks development proposal")

        search = Search.objects.get_or_create_for_params(
            search_term="parks", municipalities=[muni]
        )

        saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Parks Updates",
            notification_frequency="immediate",
        )

        check_saved_search_for_updates(saved_search.id)
        assert len(mail.outbox) == 1

        saved_search.notification_frequency = "daily"
        saved_search.save()
        mail.outbox.clear()

        _page2 = MeetingPageFactory(
            document=doc, text="Parks renovation budget approved"
        )

        check_saved_search_for_updates(saved_search.id)
        assert len(mail.outbox) == 0
        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is True

    def test_weekly_digest_only_sends_weekly(self):
        """Test that weekly digest searches don't get sent in daily digest."""
        user = UserFactory(email="weekly@example.com")
        muni = MuniFactory()
        search = Search.objects.get_or_create_for_params(
            search_term="development", municipalities=[muni]
        )

        _saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Development Weekly",
            notification_frequency="weekly",
            has_pending_results=True,
        )

        send_daily_digests()
        assert len(mail.outbox) == 0

        send_weekly_digests()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["weekly@example.com"]
