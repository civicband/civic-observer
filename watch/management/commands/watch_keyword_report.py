"""Report per-keyword f5bot yield: mentions, threads, coverage, and replies.

``covered_threads`` counts distinct threads with at least one covered subject
place, and ``replied_threads`` counts distinct threads an operator replied to.
Blank keywords are excluded, and ``--days`` bounds ``Mention.published_at``.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q
from django.utils import timezone

from watch.models import Mention, Thread, ThreadPlace

DEFAULT_DAYS = 30
NON_BLANK_KEYWORD = r"^\s*$"
KEYWORD_COLUMN = 32


def keyword_report(days: int) -> list[dict[str, object]]:
    """Return one row per keyword with mentions over the last ``days``."""
    start = timezone.now() - timedelta(days=days)
    covered_thread = Q(
        thread__thread_places__role=ThreadPlace.Role.SUBJECT,
        thread__thread_places__place__muni__isnull=False,
    )
    rows = (
        Mention.objects.filter(published_at__gte=start)
        .exclude(keyword__regex=NON_BLANK_KEYWORD)
        .values("keyword")
        .annotate(
            mentions=Count("id", distinct=True),
            threads=Count("thread", distinct=True),
            covered_threads=Count("thread", distinct=True, filter=covered_thread),
            replied_threads=Count(
                "thread",
                distinct=True,
                filter=Q(thread__status=Thread.Status.REPLIED),
            ),
        )
        .order_by("keyword")
    )
    return [
        {
            "keyword": row["keyword"],
            "mentions": row["mentions"],
            "threads": row["threads"],
            "covered_threads": row["covered_threads"],
            "replied_threads": row["replied_threads"],
        }
        for row in rows
    ]


class Command(BaseCommand):
    help = (
        "Report per f5bot keyword mentions, distinct threads, threads with a "
        "covered subject place, and replied threads. Defaults to 30 days."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help="Only include mentions published in the last N days.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit JSON instead of a plain-text table.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days <= 0:
            raise CommandError("--days must be a positive integer")

        report = keyword_report(days)
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
            return

        self.stdout.write(f"Keyword report (last {days} days)")
        self.stdout.write("")
        self.stdout.write(
            f"{'keyword':<{KEYWORD_COLUMN}}{'mentions':>9}{'threads':>9}"
            f"{'covered':>9}{'replied':>9}"
        )
        if not report:
            self.stdout.write("no mentions in this window")
            return
        for row in report:
            self.stdout.write(
                f"{row['keyword']:<{KEYWORD_COLUMN}}{row['mentions']:>9}"
                f"{row['threads']:>9}{row['covered_threads']:>9}"
                f"{row['replied_threads']:>9}"
            )
