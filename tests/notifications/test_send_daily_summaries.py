"""Tests for the send_daily_summaries management command."""

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from notifications.models import DailySummarySubscription, DigestSubscription
from searches.models import SavedSearch
from tests.factories import (
    MeetingDocumentFactory,
    MuniFactory,
    SearchFactory,
    UserFactory,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendDailySummariesCommandTest(TestCase):
    def setUp(self):
        self.user = UserFactory(
            email="testuser@example.com",
            timezone="America/New_York",
        )
        self.muni = MuniFactory(name="Testville", state="CA", subdomain="testville")
        self.digest_sub = DigestSubscription.objects.create(
            user=self.user,
            municipality=self.muni,
        )
        self.summary_sub = DailySummarySubscription.objects.create(
            user=self.user,
        )

    def test_sends_summary_with_today_meetings(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        output = out.getvalue()
        self.assertIn("Sent: 1", output)

    def test_skips_when_no_data(self):
        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        output = out.getvalue()
        self.assertIn("Sent: 0", output)

    def test_dry_run_does_not_send_email(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="Planning Board",
            document_type="agenda",
        )

        out = StringIO()
        call_command("send_daily_summaries", "--dry-run", stdout=out)

        output = out.getvalue()
        self.assertIn("[DRY RUN]", output)
        self.summary_sub.refresh_from_db()
        self.assertIsNone(self.summary_sub.last_summary_sent)

    def test_updates_last_summary_sent(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        self.summary_sub.refresh_from_db()
        self.assertEqual(self.summary_sub.last_summary_sent, today)

    def test_skips_if_already_sent_today(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        DailySummarySubscription.objects.filter(pk=self.summary_sub.pk).update(
            last_summary_sent=today,
        )

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        output = out.getvalue()
        self.assertIn("Sent: 0", output)

    def test_filter_by_user_email(self):
        today = date.today()
        user2 = UserFactory(email="other@example.com")
        muni2 = MuniFactory(name="Secondville", state="CA", subdomain="secondville")
        DigestSubscription.objects.create(user=user2, municipality=muni2)
        DailySummarySubscription.objects.create(user=user2)
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        MeetingDocumentFactory(
            municipality=muni2,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_daily_summaries", "--user", "testuser@example.com", stdout=out
        )

        output = out.getvalue()
        self.assertIn("Sent: 1", output)

    def test_inactive_subscriptions_are_skipped(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        self.summary_sub.is_active = False
        self.summary_sub.save()

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        output = out.getvalue()
        self.assertIn("No active daily summary subscriptions found", output)

    def test_auto_enrolls_users_with_existing_digests(self):
        user2 = UserFactory(email="newuser@example.com")
        muni2 = MuniFactory(name="Newtown", state="CA", subdomain="newtown")
        DigestSubscription.objects.create(user=user2, municipality=muni2)

        # Confirm no summary sub yet
        self.assertFalse(DailySummarySubscription.objects.filter(user=user2).exists())

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        output = out.getvalue()
        self.assertIn("Auto-enrolled", output)
        self.assertTrue(
            DailySummarySubscription.objects.filter(user=user2, is_active=True).exists()
        )

    def test_clears_pending_saved_searches_after_sending(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        search = SearchFactory()
        saved_search = SavedSearch.objects.create(
            user=self.user,
            search=search,
            name="Test Search",
            notification_frequency="daily",
            has_pending_results=True,
        )

        out = StringIO()
        call_command("send_daily_summaries", stdout=out)

        saved_search.refresh_from_db()
        self.assertFalse(saved_search.has_pending_results)

    def test_pending_searches_show_in_summary(self):
        search = SearchFactory(search_term="housing")
        SavedSearch.objects.create(
            user=self.user,
            search=search,
            name="Housing Alerts",
            notification_frequency="daily",
            has_pending_results=True,
        )

        out = StringIO()
        call_command("send_daily_summaries", "--dry-run", stdout=out)

        output = out.getvalue()
        self.assertIn("pending saved searches", output)
