"""Exit nonzero when the CivicBand Watch ingest is stale or quiet."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from watch.health import health_status, send_stale_alert


class Command(BaseCommand):
    help = (
        "Check ingest health for cron monitoring. Exits nonzero when the last "
        "successful run is too old or no recent run added mentions."
    )

    def handle(self, *args, **options):
        status = health_status()
        alert_sent = send_stale_alert(status)

        hours = status["hours_since_success"]
        hours_text = f"{float(hours):.1f}" if isinstance(hours, int | float) else "n/a"
        self.stdout.write(
            "watch health: "
            f"stale={status['stale']} "
            f"quiet={status['quiet']} "
            f"hours_since_success={hours_text} "
            f"alerts_today={status['alerts_today']} "
            f"alert_email_sent={alert_sent}"
        )

        problems = []
        if status["stale"]:
            problems.append("ingest is stale")
        if status["quiet"]:
            problems.append("ingest is quiet (no new mentions in 24h)")
        if problems:
            raise CommandError("; ".join(problems))
