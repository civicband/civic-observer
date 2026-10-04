"""Management command to send daily meeting digest emails at 5 AM in each user's timezone."""

import logging
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from meetings.models import MeetingDocument
from notifications.models import DigestSubscription
from notifications.services import send_meeting_digest_email

logger = logging.getLogger(__name__)

DIGEST_HOUR = 5  # 5 AM in user's local timezone


class Command(BaseCommand):
    help = "Send daily meeting digest emails at 5 AM in each user's timezone."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate sending emails without actually sending them.",
        )
        parser.add_argument(
            "--for-date",
            type=str,
            default=None,
            help="Override the target date (YYYY-MM-DD). Defaults to today in each user's timezone.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Send even if it's not currently 5 AM in the user's timezone (useful for manual runs).",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Filter to a specific user by email.",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.force = options["force"]
        self.for_date_str = options.get("for_date")
        self.email_filter = options.get("user")

        subscriptions = self._get_active_subscriptions()

        if not subscriptions:
            self.stdout.write(
                self.style.WARNING("No active digest subscriptions found.")
            )
            return

        # Group subscriptions by user timezone
        tz_groups = defaultdict(list)
        for sub in subscriptions:
            tz_groups[sub.user.timezone].append(sub)

        total_sent = 0
        total_skipped_no_meetings = 0
        total_skipped_already_sent = 0
        total_meetings_found = 0

        for tz_name, subs in tz_groups.items():
            results = self._process_timezone_group(tz_name, subs)
            total_sent += results["sent"]
            total_skipped_no_meetings += results["skipped_no_meetings"]
            total_skipped_already_sent += results["skipped_already_sent"]
            total_meetings_found += results["meetings_found"]

        # Summary
        self.stdout.write(
            self.style.SUCCESS(f"""
Digest Complete:
  Emails sent: {total_sent}
  Skipped (no meetings today): {total_skipped_no_meetings}
  Skipped (already sent): {total_skipped_already_sent}
  Total meetings found: {total_meetings_found}
        """)
        )

    def _get_active_subscriptions(self):
        """Get all active digest subscriptions, optionally filtered by user email."""
        qs = DigestSubscription.objects.filter(
            is_active=True,
        ).select_related("user", "municipality")

        if self.email_filter:
            qs = qs.filter(user__email=self.email_filter)

        return qs

    def _process_timezone_group(self, tz_name, subscriptions):
        """Process all subscriptions in a given timezone."""
        results = {
            "sent": 0,
            "skipped_no_meetings": 0,
            "skipped_already_sent": 0,
            "meetings_found": 0,
        }

        if not self.force:
            current_hour = self._get_current_hour_in_tz(tz_name)
            if current_hour != DIGEST_HOUR:
                logger.debug(
                    f"Skipping timezone {tz_name}: current hour is {current_hour}, not {DIGEST_HOUR}"
                )
                return results

        # Get local date for this timezone
        if self.for_date_str:
            local_date = date.fromisoformat(self.for_date_str)
        else:
            tz = ZoneInfo(tz_name)
            local_date = timezone.now().astimezone(tz).date()

        # Group subscriptions by user
        user_groups = defaultdict(list)
        for sub in subscriptions:
            user_groups[sub.user].append(sub)

        # Fetch every meeting for this date across the group once, then filter
        # per user in memory, instead of one query per user.
        all_muni_ids = {sub.municipality_id for sub in subscriptions}
        meetings = self._get_meetings_for_date(local_date, list(all_muni_ids))

        sent_user_ids = []

        for user, user_subs in user_groups.items():
            # Already sent today? Checked in memory (subs carry last_digest_sent).
            already_sent_today = any(
                sub.last_digest_sent == local_date for sub in user_subs
            )

            if already_sent_today:
                results["skipped_already_sent"] += len(user_subs)
                continue

            user_muni_ids = {sub.municipality_id for sub in user_subs}
            user_meetings = [
                meeting
                for meeting in meetings
                if meeting.municipality_id in user_muni_ids
            ]

            if not user_meetings:
                results["skipped_no_meetings"] += len(user_subs)
                continue

            results["meetings_found"] += len(user_meetings)

            if not self.dry_run:
                try:
                    send_meeting_digest_email(user, user_meetings, local_date)
                    sent_user_ids.append(user.id)
                    results["sent"] += 1
                    logger.info(f"Sent digest to {user.email} for {local_date}")
                except Exception as e:
                    logger.error(f"Failed to send digest to {user.email}: {e}")
            else:
                self.stdout.write(
                    f"[DRY RUN] Would send digest to {user.email} "
                    f"with {len(user_meetings)} meetings"
                )
                results["sent"] += 1

        # One write for the whole group instead of one per user.
        if sent_user_ids:
            DigestSubscription.objects.filter(
                user_id__in=sent_user_ids,
                is_active=True,
            ).update(last_digest_sent=local_date)

        return results

    def _get_current_hour_in_tz(self, tz_name):
        """Get the current hour in the given timezone."""
        tz = ZoneInfo(tz_name)
        return timezone.now().astimezone(tz).hour

    def _get_meetings_for_date(self, target_date, municipality_ids):
        """Get meetings scheduled for a specific date across given municipalities."""
        meetings = (
            MeetingDocument.objects.filter(
                meeting_date=target_date,
                municipality_id__in=municipality_ids,
            )
            .select_related("municipality")
            .order_by(
                "municipality__name",
                "meeting_name",
                "document_type",
            )
        )

        return list(meetings)
