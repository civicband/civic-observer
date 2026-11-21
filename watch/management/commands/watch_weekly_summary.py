"""Email the weekly CivicBand Watch summary to ``WATCH_SUMMARY_EMAIL``.

The body carries counts, thread titles, one place per thread, and triage links
only. It never includes mention snippets, the f5bot feed URL, or the access
hash.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from watch.models import Thread, ThreadPlace
from watch.scoring import backlog_queryset

SUMMARY_SUBJECT = "CivicBand Watch: weekly summary"
SUMMARY_DAYS = 7
REPLIED_THREAD_LIMIT = 20
NO_PLACE = "no place"


def _replied_threads(since: datetime) -> QuerySet[Thread]:
    """Return recent replied threads, newest first, with subject places.

    Places are prefetched in name order so the body can show the first one
    without an N+1 query.
    """
    return (
        Thread.objects.filter(status=Thread.Status.REPLIED, modified__gte=since)
        .prefetch_related(
            Prefetch(
                "thread_places",
                queryset=ThreadPlace.objects.filter(role=ThreadPlace.Role.SUBJECT)
                .select_related("place")
                .order_by("place__name", "place__pk"),
                to_attr="weekly_subject_places",
            )
        )
        .order_by("-modified", "-id")[:REPLIED_THREAD_LIMIT]
    )


def _thread_place_label(thread: Thread) -> str:
    """Return the thread's first subject place name, or ``no place``."""
    places = thread.weekly_subject_places  # type: ignore[attr-defined]
    return places[0].place.name if places else NO_PLACE


def _recent_count(status: str, since: datetime) -> int:
    """Return how many threads with ``status`` changed since ``since``."""
    return Thread.objects.filter(status=status, modified__gte=since).count()


def summary_body(now: datetime) -> str:
    """Build the plain-text summary body for the week ending at ``now``."""
    since = now - timedelta(days=SUMMARY_DAYS)
    lines = [
        "CivicBand Watch weekly summary",
        "",
        f"Replied in the last {SUMMARY_DAYS} days: "
        f"{_recent_count(Thread.Status.REPLIED, since)}",
        f"Dismissed in the last {SUMMARY_DAYS} days: "
        f"{_recent_count(Thread.Status.DISMISSED, since)}",
        f"New uncovered subject places in the backlog: {backlog_queryset().count()}",
        "",
        "Replied threads:",
    ]
    replied = list(_replied_threads(since))
    if not replied:
        lines.append("- none")
    for thread in replied:
        lines.extend(
            [
                f"- {thread.title or thread.key}",
                f"  Place: {_thread_place_label(thread)}",
                f"  /triage/?thread={thread.pk}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


class Command(BaseCommand):
    help = (
        "Email the weekly CivicBand Watch summary: replied and dismissed "
        "counts, backlog size, and the newest replied threads. Skips sending "
        "when WATCH_SUMMARY_EMAIL is empty."
    )

    def handle(self, *args, **options):
        recipients = list(settings.WATCH_SUMMARY_EMAIL)
        if not recipients:
            self.stdout.write("watch weekly summary: no recipients configured; skipped")
            return

        send_mail(
            SUMMARY_SUBJECT,
            summary_body(timezone.now()),
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=False,
        )
        self.stdout.write("watch weekly summary: sent")
