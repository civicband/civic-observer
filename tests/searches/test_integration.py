"""
Integration tests for the saved search system.

These tests verify the complete end-to-end workflow:
1. Creating searches with all filter types
2. Saving searches from parameters
3. Triggering notifications after new pages are ingested
4. Sending digest emails
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

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
        # Setup: Create user and municipalities
        user = UserFactory(email="test@example.com")
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")

        # Create a search with multiple filters
        search = Search.objects.get_or_create_for_params(
            search_term="housing",
            municipalities=[muni1, muni2],
            states=["CA"],
            document_type="agenda",
        )

        # Create a saved search
        saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Bay Area Housing Updates",
            notification_frequency="immediate",
        )

        # Verify no emails sent yet
        assert len(mail.outbox) == 0

        # Simulate new page ingest
        doc = MeetingDocumentFactory(
            municipality=muni1, document_type="agenda", meeting_date="2025-01-15"
        )
        _page = MeetingPageFactory(
            document=doc, text="Discussion about affordable housing programs in Oakland"
        )

        # Trigger notification check
        check_saved_search_for_updates(saved_search.id)

        # Verify email was sent
        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["test@example.com"]
        assert "housing" in email.body.lower()
        assert "Bay Area Housing Updates" in email.body

        # Verify tracking fields updated
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

        # Create two searches with daily digest
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

        # Ingest matching pages
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="Budget discussion for fiscal year 2025")
        MeetingPageFactory(document=doc, text="Proposed zoning changes for downtown")

        # Check searches and flag for digest
        check_saved_search_for_updates(saved_search1.id)
        check_saved_search_for_updates(saved_search2.id)

        # Verify no immediate emails sent
        assert len(mail.outbox) == 0

        # Verify searches flagged
        saved_search1.refresh_from_db()
        saved_search2.refresh_from_db()
        assert saved_search1.has_pending_results is True
        assert saved_search2.has_pending_results is True

        # Run daily digest
        send_daily_digests()

        # Verify combined email sent
        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["digest@example.com"]
        assert "Budget Watch" in email.body
        assert "Zoning Changes" in email.body
        assert "daily" in email.subject.lower()

        # Verify flags cleared
        saved_search1.refresh_from_db()
        saved_search2.refresh_from_db()
        assert saved_search1.has_pending_results is False
        assert saved_search2.has_pending_results is False

    def test_batch_notification_after_ingest(self):
        """
        Test that after ingesting multiple pages, all immediate searches are checked.
        """
        # Create multiple users with immediate searches
        user1 = UserFactory(email="user1@example.com")
        user2 = UserFactory(email="user2@example.com")
        muni = MuniFactory(name="Portland", state="OR")

        search1 = Search.objects.get_or_create_for_params(
            search_term="transportation", municipalities=[muni]
        )
        search2 = Search.objects.get_or_create_for_params(
            search_term="",
            municipalities=[muni],  # All updates mode
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

        # Ingest a page that matches both searches
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(
            document=doc,
            text="New light rail transportation project proposal for downtown",
        )

        # Run batch check (would be triggered after ingest)
        check_all_immediate_searches()

        # Verify both users received emails
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
            search_term="",  # All updates mode
            municipalities=[muni1, muni2],
        )

        _saved_search = SavedSearch.objects.create(
            user=user, search=search, name="All Texas Updates"
        )

        # Create pages in both municipalities
        doc1 = MeetingDocumentFactory(municipality=muni1)
        doc2 = MeetingDocumentFactory(municipality=muni2)
        page1 = MeetingPageFactory(document=doc1, text="Austin city council agenda")
        page2 = MeetingPageFactory(document=doc2, text="Dallas planning meeting")

        # Update search should find both
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

        # Create pages with different dates
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

        # Update search should only find page in range
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

        # Create document and page first
        doc = MeetingDocumentFactory(municipality=muni)
        _page1 = MeetingPageFactory(document=doc, text="New parks development proposal")

        # Create search after page exists
        search = Search.objects.get_or_create_for_params(
            search_term="parks", municipalities=[muni]
        )

        # Start with immediate
        saved_search = SavedSearch.objects.create(
            user=user,
            search=search,
            name="Parks Updates",
            notification_frequency="immediate",
        )

        # Check with immediate - should send email for existing page
        check_saved_search_for_updates(saved_search.id)
        assert len(mail.outbox) == 1

        # Change to daily
        saved_search.notification_frequency = "daily"
        saved_search.save()
        mail.outbox.clear()

        # Ingest another page
        _page2 = MeetingPageFactory(
            document=doc, text="Parks renovation budget approved"
        )

        # Check with daily - should NOT send email, just flag
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

        # Run daily digest
        send_daily_digests()
        assert len(mail.outbox) == 0  # Should not send

        # Run weekly digest
        send_weekly_digests()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["weekly@example.com"]
