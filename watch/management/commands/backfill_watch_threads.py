"""Enqueue enrichment for existing threads so they gain places and hits."""

from __future__ import annotations

from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watch.ingest import enqueue_enrichment
from watch.models import Thread


class Command(BaseCommand):
    help = (
        "Enqueue enrichment for existing watched threads, newest first. "
        "By default only threads without ThreadPlace rows are selected."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_threads",
            help="Include threads that already have ThreadPlace rows.",
        )
        parser.add_argument(
            "--since",
            metavar="YYYY-MM-DD",
            help="Only include threads last seen on or after this date.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Enqueue at most this many threads.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the selection without enqueuing any jobs.",
        )

    def handle(self, *args, **options):
        since = self._parse_since(options.get("since"))
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer")

        threads = Thread.objects.all()
        if since is not None:
            start = timezone.make_aware(datetime.combine(since, time.min))
            threads = threads.filter(last_seen__gte=start)

        if options["all_threads"]:
            skipped = 0
            selected = threads
        else:
            skipped = threads.filter(thread_places__isnull=False).distinct().count()
            selected = threads.filter(thread_places__isnull=True)

        selected = selected.order_by("-last_seen", "-id")
        if limit is not None:
            selected = selected[:limit]

        selected_count = enqueued = failed = 0
        for thread in selected:
            selected_count += 1
            if options["dry_run"]:
                continue
            if enqueue_enrichment(str(thread.id)) is None:
                failed += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"Could not enqueue enrichment for {thread.key} "
                        f"({thread.id}); is the queue available?"
                    )
                )
            else:
                enqueued += 1

        if options["dry_run"]:
            summary = (
                f"dry-run selected={selected_count} enqueued=0 "
                f"skipped={skipped} failed=0"
            )
        else:
            summary = f"enqueued={enqueued} skipped={skipped} failed={failed}"
        self.stdout.write(summary)

    def _parse_since(self, raw: str | None) -> date | None:
        """Parse ``--since`` as an ISO date, rejecting malformed values."""
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError("--since must be a YYYY-MM-DD date") from exc
