"""Tests for the send_meeting_digests management command."""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from notifications.models import DigestSubscription
from tests.factories import MeetingDocumentFactory, MuniFactory, UserFactory


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendMeetingDigestsCommandTest(TestCase):
    def setUp(self):
        self.user = UserFactory(
            email="testuser@example.com",
            timezone="America/New_York",
        )
        self.muni = MuniFactory(name="Testville", state="CA", subdomain="testville")
        self.sub = DigestSubscription.objects.create(
            user=self.user,
            municipality=self.muni,
        )

    def test_sends_digest_when_meetings_exist_today(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 1", output)
        self.assertIn("Total meetings found: 1", output)

    def test_skips_email_when_no_meetings_today(self):
        today = date.today()
        tomorrow = today + timedelta(days=1)
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=tomorrow,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 0", output)
        self.assertIn("Skipped (no meetings today)", output)

    def test_dry_run_does_not_send_email(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="Planning Board",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            "--dry-run",
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("[DRY RUN]", output)
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.last_digest_sent)

    def test_updates_last_digest_sent_after_sending(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.last_digest_sent, today)

    def test_skips_if_already_sent_today(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        DigestSubscription.objects.filter(pk=self.sub.pk).update(
            last_digest_sent=today,
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Skipped (already sent)", output)
        self.assertIn("Emails sent: 0", output)

    def test_force_bypasses_timezone_hour_check(self):
        """--force should bypass the 5 AM timezone hour check."""
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 1", output)

    def test_no_force_skips_when_not_five_am(self):
        """Without --force, the command should skip if it's not 5 AM in user's timezone."""
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 0", output)

    def test_includes_multiple_meetings_for_same_municipality(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="Planning Board",
            document_type="minutes",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Total meetings found: 2", output)

    def test_groups_multiple_municipalities_single_email(self):
        today = date.today()
        muni2 = MuniFactory(name="Othertown", state="CA", subdomain="othertown")
        DigestSubscription.objects.create(
            user=self.user,
            municipality=muni2,
        )
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        MeetingDocumentFactory(
            municipality=muni2,
            meeting_date=today,
            meeting_name="Town Board",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Total meetings found: 2", output)

    def test_filter_by_user_email(self):
        today = date.today()
        user2 = UserFactory(email="other@example.com", timezone="America/New_York")
        muni2 = MuniFactory(name="Secondville", state="CA", subdomain="secondville")
        DigestSubscription.objects.create(
            user=user2,
            municipality=muni2,
        )
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
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            "--user",
            "testuser@example.com",
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Total meetings found: 1", output)

    def test_inactive_subscriptions_are_skipped(self):
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        self.sub.is_active = False
        self.sub.save()

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("No active digest subscriptions found", output)

    def test_timezone_hour_check_skips_when_not_five_am(self):
        """Outside 5 AM window, the command should skip sending."""
        today = date.today()
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 0", output)

    def test_multiple_users_same_timezone_one_email_each(self):
        today = date.today()
        user2 = UserFactory(
            email="alice@example.com",
            timezone="America/New_York",
        )
        muni2 = MuniFactory(name="Westfield", state="CA", subdomain="westfield")
        DigestSubscription.objects.create(
            user=user2,
            municipality=muni2,
        )
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=today,
            meeting_name="City Council",
            document_type="agenda",
        )
        MeetingDocumentFactory(
            municipality=muni2,
            meeting_date=today,
            meeting_name="Town Board",
            document_type="agenda",
        )

        out = StringIO()
        call_command(
            "send_meeting_digests",
            "--force",
            "--for-date",
            str(today),
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Emails sent: 2", output)
