"""
Management command to send weekly digest notifications.

This command should be scheduled to run once per week via cron.
Example crontab entry (runs at 9 AM every Monday):
    0 9 * * 1 cd /path/to/project && uv run python manage.py send_weekly_digests
"""

from django.core.management.base import BaseCommand

from searches.tasks import send_weekly_digests


class Command(BaseCommand):
    help = "Send weekly digest notifications to users with pending saved search results"

    def handle(self, *args, **options):
        self.stdout.write("Starting weekly digest task...")
        result = send_weekly_digests()

        emails_sent = result.get("emails_sent", 0)
        searches_notified = result.get("searches_notified", 0)

        self.stdout.write(
            self.style.SUCCESS(
                f"Weekly digests complete: {emails_sent} emails sent for {searches_notified} searches"
            )
        )
