"""Tests for notifications/services.py daily summary email sending."""

from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from notifications.services import send_daily_summary_email
from tests.factories import MeetingDocumentFactory, MuniFactory, UserFactory


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendDailySummaryEmailTest(TestCase):
    def setUp(self):
        self.user = UserFactory(
            email="summaryuser@example.com",
            timezone="America/Chicago",
        )
        self.muni = MuniFactory(name="Springfield", state="IL", subdomain="springfield")
        self.today = date.today()

    def test_email_groups_meetings_by_municipality(self):
        muni2 = MuniFactory(name="Shelbyville", state="IL", subdomain="shelbyville")
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=self.today,
            meeting_name="City Council",
            document_type="agenda",
        )
        MeetingDocumentFactory(
            municipality=muni2,
            meeting_date=self.today,
            meeting_name="Town Board",
            document_type="agenda",
        )
        from meetings.models import MeetingDocument

        today_meetings = list(
            MeetingDocument.objects.filter(meeting_date=self.today).select_related(
                "municipality"
            ),
        )
        send_daily_summary_email(
            user=self.user,
            today_meetings=today_meetings,
            recent_docs=[],
            pending_searches=[],
            summary_date=self.today,
        )

        html_content = mail.outbox[0].alternatives[0][0]
        self.assertIn("Springfield", html_content)
        self.assertIn("Shelbyville", html_content)
        self.assertIn("City Council", html_content)
        self.assertIn("Town Board", html_content)

    def test_uses_default_from_email_setting(self):
        meetings = [
            MeetingDocumentFactory(
                municipality=self.muni,
                meeting_date=self.today,
                meeting_name="City Council",
                document_type="agenda",
            )
        ]
        send_daily_summary_email(
            user=self.user,
            today_meetings=meetings,
            recent_docs=[],
            pending_searches=[],
            summary_date=self.today,
        )

        self.assertIn("noreply@civic.observer", mail.outbox[0].from_email)

    def test_includes_recent_docs_section(self):
        MeetingDocumentFactory(
            municipality=self.muni,
            meeting_date=self.today,
            meeting_name="Planning Board",
            document_type="minutes",
        )
        from meetings.models import MeetingDocument

        recent_docs = list(
            MeetingDocument.objects.filter(meeting_date=self.today).select_related(
                "municipality"
            ),
        )
        send_daily_summary_email(
            user=self.user,
            today_meetings=[],
            recent_docs=recent_docs,
            pending_searches=[],
            summary_date=self.today,
        )

        html_content = mail.outbox[0].alternatives[0][0]
        self.assertIn("Recently Published", html_content)
        self.assertIn("Planning Board", html_content)
