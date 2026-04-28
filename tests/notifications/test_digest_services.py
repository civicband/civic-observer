"""Tests for notifications/services.py digest email sending."""

from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from notifications.models import DigestSubscription
from notifications.services import send_meeting_digest_email
from tests.factories import MeetingDocumentFactory, MuniFactory, UserFactory


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendMeetingDigestEmailTest(TestCase):
    def setUp(self):
        self.user = UserFactory(
            email="digestuser@example.com",
            timezone="America/Chicago",
        )
        self.muni = MuniFactory(name="Springfield", state="IL", subdomain="springfield")
        self.today = date.today()

    def _create_meetings(self, count=2):
        meeting_names = [
            "City Council",
            "Planning Board",
            "Zoning Committee",
            "School Board",
        ]
        return [
            MeetingDocumentFactory(
                municipality=self.muni,
                meeting_date=self.today,
                meeting_name=meeting_names[i],
                document_type="agenda",
            )
            for i in range(count)
        ]

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

        all_meetings = list(
            MeetingDocument.objects.filter(
                meeting_date=self.today,
            ).select_related("municipality"),
        )
        send_meeting_digest_email(self.user, all_meetings, self.today)

        html_content = mail.outbox[0].alternatives[0][0]  # type: ignore[attr-defined]
        self.assertIn("Springfield", html_content)
        self.assertIn("Shelbyville", html_content)
        self.assertIn("City Council", html_content)
        self.assertIn("Town Board", html_content)

    def test_uses_default_from_email_setting(self):
        meetings = self._create_meetings(1)
        send_meeting_digest_email(self.user, meetings, self.today)

        self.assertIn("noreply@civic.observer", mail.outbox[0].from_email)

    def test_does_not_update_last_digest_sent(self):
        """Service should NOT update last_digest_sent (that's the command's job)."""
        DigestSubscription.objects.create(
            user=self.user,
            municipality=self.muni,
        )
        meetings = self._create_meetings(1)
        send_meeting_digest_email(self.user, meetings, self.today)

        sub = DigestSubscription.objects.get(user=self.user, municipality=self.muni)
        self.assertIsNone(sub.last_digest_sent)
