"""
Tests for scheduled digest notification tasks.

These tests verify that:
1. Daily digests are sent to users with pending results
2. Weekly digests are sent to users with pending results
3. Digests are only sent when has_pending_results=True
4. has_pending_results is cleared after sending
5. Multiple saved searches for same user are combined in one email
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
class TestDailyDigestTask:
    """Test daily digest email generation and sending."""

    def test_send_daily_digest_with_pending_results(self):
        """
        When a saved search has notification_frequency='daily' and has_pending_results=True,
        the daily digest task should send an email.
        """
        user = UserFactory(email="daily@example.com")
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="daily",
            has_pending_results=True,
        )

        # Create some matching pages
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(document=doc, text="Budget discussion for 2025")

        # Mark search as already checked (page created before this timestamp)
        from django.utils import timezone

        search.last_checked_for_new_pages = timezone.now()
        search.save()

        # Run daily digest task
        from searches.tasks import send_daily_digests

        send_daily_digests()

        # Verify email was sent
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["daily@example.com"]

        # Verify has_pending_results was cleared
        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is False
        assert saved_search.last_notification_sent is not None

    def test_no_digest_sent_without_pending_results(self):
        """
        When has_pending_results=False, no daily digest should be sent.
        """
        user = UserFactory(email="daily@example.com")
        search = SearchFactory(search_term="budget")
        SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="daily",
            has_pending_results=False,
        )

        from searches.tasks import send_daily_digests

        send_daily_digests()

        # No emails should be sent
        assert len(mail.outbox) == 0

    def test_daily_digest_combines_multiple_searches(self):
        """
        When a user has multiple saved searches with pending results,
        they should receive one combined daily digest email.
        """
        user = UserFactory(email="multi@example.com")

        # Create two saved searches with pending results
        search1 = SearchFactory(search_term="budget")
        search2 = SearchFactory(search_term="zoning")

        SavedSearchFactory(
            user=user,
            search=search1,
            notification_frequency="daily",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=search2,
            notification_frequency="daily",
            has_pending_results=True,
        )

        from searches.tasks import send_daily_digests

        send_daily_digests()

        # Should send exactly one email
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["multi@example.com"]

    def test_daily_digest_only_sends_to_daily_frequency(self):
        """
        Daily digest should only send to saved searches with notification_frequency='daily',
        not 'weekly' or 'immediate'.
        """
        user = UserFactory(email="test@example.com")

        # Create searches with different frequencies, all with pending results
        daily_search = SearchFactory(search_term="budget")
        weekly_search = SearchFactory(search_term="zoning")
        immediate_search = SearchFactory(search_term="housing")

        SavedSearchFactory(
            user=user,
            search=daily_search,
            notification_frequency="daily",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=weekly_search,
            notification_frequency="weekly",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=immediate_search,
            notification_frequency="immediate",
            has_pending_results=True,
        )

        from searches.tasks import send_daily_digests

        send_daily_digests()

        # Should only send one email (for daily search)
        assert len(mail.outbox) == 1


@pytest.mark.django_db
class TestWeeklyDigestTask:
    """Test weekly digest email generation and sending."""

    def test_send_weekly_digest_with_pending_results(self):
        """
        When a saved search has notification_frequency='weekly' and has_pending_results=True,
        the weekly digest task should send an email.
        """
        user = UserFactory(email="weekly@example.com")
        search = SearchFactory(search_term="housing")
        saved_search = SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="weekly",
            has_pending_results=True,
        )

        # Create some matching pages
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        _page = MeetingPageFactory(document=doc, text="Housing development proposal")

        # Mark search as already checked (page created before this timestamp)
        from django.utils import timezone

        search.last_checked_for_new_pages = timezone.now()
        search.save()

        # Run weekly digest task
        from searches.tasks import send_weekly_digests

        send_weekly_digests()

        # Verify email was sent
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["weekly@example.com"]

        # Verify has_pending_results was cleared
        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is False
        assert saved_search.last_notification_sent is not None

    def test_no_weekly_digest_without_pending_results(self):
        """
        When has_pending_results=False, no weekly digest should be sent.
        """
        user = UserFactory(email="weekly@example.com")
        search = SearchFactory(search_term="budget")
        SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="weekly",
            has_pending_results=False,
        )

        from searches.tasks import send_weekly_digests

        send_weekly_digests()

        # No emails should be sent
        assert len(mail.outbox) == 0

    def test_weekly_digest_combines_multiple_searches(self):
        """
        When a user has multiple weekly saved searches with pending results,
        they should receive one combined weekly digest email.
        """
        user = UserFactory(email="multi-weekly@example.com")

        # Create two saved searches with pending results
        search1 = SearchFactory(search_term="budget")
        search2 = SearchFactory(search_term="zoning")

        SavedSearchFactory(
            user=user,
            search=search1,
            notification_frequency="weekly",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=search2,
            notification_frequency="weekly",
            has_pending_results=True,
        )

        from searches.tasks import send_weekly_digests

        send_weekly_digests()

        # Should send exactly one email
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["multi-weekly@example.com"]

    def test_weekly_digest_only_sends_to_weekly_frequency(self):
        """
        Weekly digest should only send to saved searches with notification_frequency='weekly',
        not 'daily' or 'immediate'.
        """
        user = UserFactory(email="test@example.com")

        # Create searches with different frequencies, all with pending results
        daily_search = SearchFactory(search_term="budget")
        weekly_search = SearchFactory(search_term="zoning")
        immediate_search = SearchFactory(search_term="housing")

        SavedSearchFactory(
            user=user,
            search=daily_search,
            notification_frequency="daily",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=weekly_search,
            notification_frequency="weekly",
            has_pending_results=True,
        )
        SavedSearchFactory(
            user=user,
            search=immediate_search,
            notification_frequency="immediate",
            has_pending_results=True,
        )

        from searches.tasks import send_weekly_digests

        send_weekly_digests()

        # Should only send one email (for weekly search)
        assert len(mail.outbox) == 1


@pytest.mark.django_db
class TestDigestEmailContent:
    """Test the content and structure of digest emails."""

    def test_digest_email_includes_all_pending_searches(self):
        """
        Digest email should include information about all pending saved searches.
        """
        user = UserFactory(email="content@example.com")

        search1 = SearchFactory(search_term="budget")
        search2 = SearchFactory(search_term="zoning")

        SavedSearchFactory(
            user=user,
            search=search1,
            notification_frequency="daily",
            has_pending_results=True,
            name="Budget Monitoring",
        )
        SavedSearchFactory(
            user=user,
            search=search2,
            notification_frequency="daily",
            has_pending_results=True,
            name="Zoning Updates",
        )

        from searches.tasks import send_daily_digests

        send_daily_digests()

        # Check email content includes both searches
        assert len(mail.outbox) == 1
        email_body = mail.outbox[0].body
        assert "Budget Monitoring" in email_body
        assert "Zoning Updates" in email_body

    def test_digest_email_subject_indicates_frequency(self):
        """
        Digest email subject should indicate whether it's daily or weekly.
        """
        user = UserFactory(email="subject@example.com")
        search = SearchFactory(search_term="budget")

        SavedSearchFactory(
            user=user,
            search=search,
            notification_frequency="daily",
            has_pending_results=True,
        )

        from searches.tasks import send_daily_digests

        send_daily_digests()

        assert len(mail.outbox) == 1
        assert "daily" in mail.outbox[0].subject.lower()
