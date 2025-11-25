"""
Management command to send daily digest notifications.

This command should be scheduled to run once per day via cron.
Example crontab entry (runs at 9 AM daily):
    0 9 * * * cd /path/to/project && uv run python manage.py send_daily_digests
"""

from django.core.management.base import BaseCommand

from searches.tasks import send_daily_digests


class Command(BaseCommand):
    help = "Send daily digest notifications to users with pending saved search results"

    def handle(self, *args, **options):
        self.stdout.write("Starting daily digest task...")
        result = send_daily_digests()

        emails_sent = result.get("emails_sent", 0)
        searches_notified = result.get("searches_notified", 0)

        self.stdout.write(
            self.style.SUCCESS(
                f"Daily digests complete: {emails_sent} emails sent for {searches_notified} searches"
            )
        )
