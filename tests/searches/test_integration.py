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

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from searches.models import SavedSearch, Search
from searches.tasks import (
    check_saved_search_for_updates,
    check_saved_searches,
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


@pytest.mark.django_db
class TestEndToEndWorkflow:
    """Test complete workflow from search creation to notification."""

    def test_complete_immediate_notification_workflow(self):
        """
        Test the complete workflow:
        1. Create a search with multiple filters
        2. Create a saved search with immediate notifications
        3. Ingest new pages that match
        4. Verify notification is sent
        """
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

    def test_complete_daily_digest_workflow(self):
        """
        Test daily digest workflow:
        1. Create multiple saved searches with daily digest
        2. Ingest pages that match
        3. Verify has_pending_results is set
        4. Run daily digest task
        5. Verify combined email is sent
        """
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

    def test_batch_notification_after_ingest(self):
        """
        Test that after ingesting multiple pages, all immediate searches are checked.
        """
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

        check_saved_searches()

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

    def test_all_updates_mode_with_multiple_municipalities(self):
        """Test all updates mode (empty search_term) with multiple municipalities."""
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

    def test_date_range_filtering(self):
        """Test that date range filters work correctly."""
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

    def test_switching_notification_frequency(self):
        """Test that changing notification frequency works correctly."""
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

    def test_notification_only_includes_pages_modified_after_cutoff(self):
        """Old pages are excluded from a cutoff-scoped notification."""
        from django.utils import timezone

        user = UserFactory(email="cutoff@example.com")
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        old_page = MeetingPageFactory(
            document=doc, page_number=1, text="Parks old proposal"
        )
        search = Search.objects.get_or_create_for_params(
            search_term="parks", municipalities=[muni]
        )
        saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Parks Updates",
            notification_frequency="immediate",
        )

        cutoff = timezone.now()
        saved_search.last_checked_for_new_pages = cutoff
        saved_search.save()
        new_page = MeetingPageFactory(
            document=doc, page_number=2, text="Parks new proposal"
        )

        check_saved_search_for_updates(saved_search.id)

        assert old_page.modified < cutoff
        assert new_page.modified >= cutoff
        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "NEW RESULTS (1)" in body
        assert "Page 2" in body
        assert "Page 1" not in body

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
