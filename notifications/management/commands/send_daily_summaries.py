"""Management command to send daily summary emails.

Combines today's meetings, recently published documents, and
pending saved search results into one consolidated email per user.

Usage:
    python manage.py send_daily_summaries
    python manage.py send_daily_summaries --dry-run
    python manage.py send_daily_summaries --user user@example.com
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from meetings.models import MeetingDocument
from notifications.models import DailySummarySubscription, DigestSubscription
from notifications.services import send_daily_summary_email
from searches.models import SavedSearch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send daily summary emails (meetings, recent docs, saved searches)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate sending emails without actually sending them.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Filter to a specific user by email.",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.email_filter = options.get("user")

        # Auto-enroll users with active DigestSubscriptions who don't have a summary sub
        self._auto_enroll_users()

        # Get active subscribers
        subscriptions = self._get_active_subscriptions()

        if not subscriptions:
            self.stdout.write(
                self.style.WARNING("No active daily summary subscriptions found.")
            )
            return

        today = timezone.now().date()
        yesterday = today - timedelta(days=1)

        total_sent = 0
        total_skipped = 0

        for sub in subscriptions:
            result = self._process_user(sub, today, yesterday)
            if result["sent"]:
                total_sent += 1
            else:
                total_skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDaily Summary Complete:\n  Sent: {total_sent}\n  Skipped: {total_skipped}"
            )
        )

    def _auto_enroll_users(self):
        """Auto-create active DailySummarySubscription for users with active DigestSubscriptions."""
        users_with_digests = set(
            DigestSubscription.objects.filter(is_active=True)
            .values_list("user_id", flat=True)
            .distinct()
        )
        existing_summary_users = set(
            DailySummarySubscription.objects.values_list("user_id", flat=True)
        )

        new_users = users_with_digests - existing_summary_users
        if new_users:
            DailySummarySubscription.objects.bulk_create(
                [DailySummarySubscription(user_id=uid) for uid in new_users]
            )
            self.stdout.write(
                f"  Auto-enrolled {len(new_users)} user(s) with existing digest subscriptions."
            )

    def _get_active_subscriptions(self):
        qs = DailySummarySubscription.objects.filter(is_active=True).select_related(
            "user"
        )
        if self.email_filter:
            qs = qs.filter(user__email=self.email_filter)
        return qs

    def _process_user(self, sub, today, yesterday):
        user = sub.user
        already_sent = DailySummarySubscription.objects.filter(
            user=user, last_summary_sent=today
        ).exists()
        if already_sent:
            return {"sent": False}

        # Get user's subscribed municipalities
        muni_ids = list(
            DigestSubscription.objects.filter(user=user, is_active=True).values_list(
                "municipality_id", flat=True
            )
        )

        if not muni_ids:
            return {"sent": False}

        # Section 1: Today's meetings
        today_meetings = (
            MeetingDocument.objects.filter(
                meeting_date=today,
                municipality_id__in=muni_ids,
            )
            .select_related("municipality")
            .order_by("municipality__name", "meeting_name", "document_type")
        )

        # Section 2: Recently published documents (last 24h)
        recent_docs = (
            MeetingDocument.objects.filter(
                created__gte=yesterday,
                municipality_id__in=muni_ids,
            )
            .exclude(meeting_date=today)
            .select_related("municipality")
            .order_by("municipality__name", "meeting_name", "document_type")
        )

        # Section 3: Pending saved search results
        pending_searches = SavedSearch.objects.filter(
            user=user, has_pending_results=True
        ).select_related("search")

        if (
            not today_meetings.exists()
            and not recent_docs.exists()
            and not pending_searches.exists()
        ):
            return {"sent": False}

        if not self.dry_run:
            try:
                send_daily_summary_email(
                    user=user,
                    today_meetings=list(today_meetings),
                    recent_docs=list(recent_docs),
                    pending_searches=list(pending_searches),
                    summary_date=today,
                )
                DailySummarySubscription.objects.filter(user=user).update(
                    last_summary_sent=today
                )

                # Clear pending flags for notified searches
                if pending_searches.exists():
                    SavedSearch.objects.filter(
                        user=user, has_pending_results=True
                    ).update(
                        has_pending_results=False, last_notification_sent=timezone.now()
                    )

                logger.info(f"Sent daily summary to {user.email}")
            except Exception as e:
                logger.error(f"Failed to send daily summary to {user.email}: {e}")
                return {"sent": False}
        else:
            parts = []
            if today_meetings.exists():
                parts.append(f"{today_meetings.count()} meetings today")
            if recent_docs.exists():
                parts.append(f"{recent_docs.count()} recently published docs")
            if pending_searches.exists():
                parts.append(f"{pending_searches.count()} pending saved searches")
            self.stdout.write(
                f"  [DRY RUN] Would send to {user.email}: {', '.join(parts)}"
            )

        return {"sent": True}
