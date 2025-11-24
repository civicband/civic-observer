"""
Tests for saved search notification tasks.

These tests verify that:
1. Saved searches are checked after new pages are ingested
2. Immediate notifications are sent when new results are found
3. No notifications are sent when there are no new results
4. Digest notifications are flagged but not sent immediately
"""

import pytest
from django.core import mail

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    SavedSearchFactory,
    SearchFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCheckSavedSearchesAfterIngest:
    """Test checking saved searches after new pages are ingested."""

    def test_immediate_notification_sent_for_new_results(self):
        """
        When new pages match a saved search with immediate notification,
        an email should be sent.
        """
        # Create a user and saved search
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="immediate",
        )

        # Create a matching page (new ingest)
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(
            document=doc,
            text="This page discusses the 2025 budget proposal.",
        )

        # Simulate checking the saved search (would be triggered by ingest)
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify email was sent
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["test@example.com"]
        assert "new results" in mail.outbox[0].subject.lower()

        # Verify tracking fields updated
        saved_search.refresh_from_db()
        assert saved_search.last_notification_sent is not None
        assert saved_search.has_pending_results is False

    def test_no_notification_when_no_new_results(self):
        """
        When a saved search has no new results, no email should be sent.
        """
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="immediate",
        )

        # Create a page and mark it as already seen
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        page = MeetingPageFactory(
            document=doc,
            text="This page discusses the 2025 budget proposal.",
        )

        # Mark the search as having already seen this page
        search.last_result_page_ids = [page.id]
        search.save()

        # Check the saved search
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify no email was sent
        assert len(mail.outbox) == 0

        # Verify tracking fields
        saved_search.refresh_from_db()
        assert saved_search.last_notification_sent is None
        assert saved_search.has_pending_results is False

    def test_daily_digest_flagged_but_not_sent(self):
        """
        When a saved search has daily digest frequency and new results,
        it should be flagged but not sent immediately.
        """
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="daily",
        )

        # Create a matching page
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(
            document=doc,
            text="This page discusses the 2025 budget proposal.",
        )

        # Check the saved search
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify no immediate email was sent
        assert len(mail.outbox) == 0

        # Verify search was flagged for digest
        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is True
        assert saved_search.last_notification_sent is None

    def test_weekly_digest_flagged_but_not_sent(self):
        """
        When a saved search has weekly digest frequency and new results,
        it should be flagged but not sent immediately.
        """
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="zoning")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="weekly",
        )

        # Create a matching page
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(
            document=doc,
            text="New zoning regulations for residential areas.",
        )

        # Check the saved search
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify no immediate email was sent
        assert len(mail.outbox) == 0

        # Verify search was flagged for digest
        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is True

    def test_all_results_mode_immediate_notification(self):
        """
        Saved searches with empty search_term (all results mode) should
        trigger notifications for any new pages in their municipalities.
        """
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="")  # All results mode
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="immediate",
        )

        # Create a page in the monitored municipality
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(
            document=doc,
            text="Any content should match in all results mode.",
        )

        # Check the saved search
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify email was sent
        assert len(mail.outbox) == 1
        assert (
            "All updates" in mail.outbox[0].subject
            or "New Results" in mail.outbox[0].subject
        )

    def test_multiple_new_pages_in_one_notification(self):
        """
        When multiple new pages match, they should all be included in
        a single notification email.
        """
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="housing")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="immediate",
        )

        # Create multiple matching pages
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        page1 = MeetingPageFactory(
            document=doc,
            page_number=1,
            text="Discussion about affordable housing programs.",
        )
        page2 = MeetingPageFactory(
            document=doc,
            page_number=2,
            text="Housing development in the downtown area.",
        )

        # Check the saved search
        from searches.tasks import check_saved_search_for_updates

        check_saved_search_for_updates(saved_search.id)

        # Verify only one email was sent
        assert len(mail.outbox) == 1

        # Verify both pages are tracked
        search.refresh_from_db()
        assert page1.id in search.last_result_page_ids
        assert page2.id in search.last_result_page_ids


@pytest.mark.django_db
class TestNotificationEdgeCases:
    """Test edge cases and error handling in notification tasks."""

    def test_invalid_saved_search_id(self):
        """
        When check_saved_search_for_updates is called with a non-existent UUID,
        it should log an error and return gracefully without crashing.
        """
        import uuid

        from searches.tasks import check_saved_search_for_updates

        # Use a valid UUID format that doesn't exist in database
        non_existent_uuid = str(uuid.uuid4())

        # This should not raise an exception
        result = check_saved_search_for_updates(non_existent_uuid)

        # Should return None without crashing
        assert result is None

    def test_no_email_sent_for_invalid_search(self):
        """
        Non-existent SavedSearch ID should not send any emails.
        """
        import uuid

        from django.core import mail

        from searches.tasks import check_saved_search_for_updates

        # Use a valid UUID format that doesn't exist
        non_existent_uuid = str(uuid.uuid4())

        check_saved_search_for_updates(non_existent_uuid)

        # No emails should be sent
        assert len(mail.outbox) == 0


@pytest.mark.django_db
class TestCheckAllSavedSearches:
    """Test batch checking of all saved searches (triggered after ingest)."""

    def test_check_all_immediate_searches(self):
        """
        After ingest, all saved searches with immediate frequency should be checked.
        """
        # Create multiple users with saved searches
        user1 = UserFactory(email="user1@example.com")
        user2 = UserFactory(email="user2@example.com")

        search1 = SearchFactory(search_term="budget")
        search2 = SearchFactory(search_term="zoning")

        _saved_search1 = SavedSearchFactory(
            user=user1,
            search=search1,
            notification_frequency="immediate",
        )
        _saved_search2 = SavedSearchFactory(
            user=user2,
            search=search2,
            notification_frequency="immediate",
        )

        # Create matching pages for both searches
        doc = MeetingDocumentFactory()
        search1.municipalities.add(doc.municipality)
        search2.municipalities.add(doc.municipality)

        _page1 = MeetingPageFactory(
            document=doc,
            page_number=1,
            text="The budget committee met to discuss funding.",
        )
        _page2 = MeetingPageFactory(
            document=doc,
            page_number=2,
            text="New zoning regulations were proposed.",
        )

        # Trigger batch check (would be called after ingest)
        from searches.tasks import check_all_immediate_searches

        check_all_immediate_searches()

        # Verify both users received emails
        assert len(mail.outbox) == 2
        recipient_emails = {msg.to[0] for msg in mail.outbox}
        assert recipient_emails == {"user1@example.com", "user2@example.com"}

    def test_only_immediate_searches_checked(self):
        """
        Batch check should only process saved searches with immediate frequency.
        """
        user = UserFactory(email="test@example.com")

        # Create saved searches with different frequencies
        immediate_search = SearchFactory(search_term="budget")
        daily_search = SearchFactory(search_term="zoning")
        weekly_search = SearchFactory(search_term="housing")

        SavedSearchFactory(
            user=user,
            search=immediate_search,
            notification_frequency="immediate",
        )
        SavedSearchFactory(
            user=user,
            search=daily_search,
            notification_frequency="daily",
        )
        SavedSearchFactory(
            user=user,
            search=weekly_search,
            notification_frequency="weekly",
        )

        # Create matching pages for all searches
        doc = MeetingDocumentFactory()
        immediate_search.municipalities.add(doc.municipality)
        daily_search.municipalities.add(doc.municipality)
        weekly_search.municipalities.add(doc.municipality)

        MeetingPageFactory(
            document=doc,
            page_number=1,
            text="Budget, zoning, and housing were all discussed.",
        )

        # Trigger batch check
        from searches.tasks import check_all_immediate_searches

        check_all_immediate_searches()

        # Only the immediate search should have sent an email
        assert len(mail.outbox) == 1
        assert "budget" in mail.outbox[0].body.lower()
