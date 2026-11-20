"""Refresh cached meeting hits for watched threads."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from watch.lookup import refresh_thread_hits
from watch.models import Thread, ThreadPlace


class Command(BaseCommand):
    help = "Refresh cached meeting hits for watched threads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--thread",
            action="append",
            dest="threads",
            metavar="UUID",
            help=(
                "Refresh one thread; repeat to refresh several. "
                "Cannot be combined with --all or --limit."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_threads",
            help=(
                "Refresh every thread with a covered subject place. "
                "Cannot be combined with --thread."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            help=(
                "With --all, refresh at most this many threads. "
                "Cannot be used with --thread."
            ),
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer")

        thread_ids = options["threads"] or []
        if thread_ids and options["all_threads"]:
            raise CommandError("--thread cannot be combined with --all")
        if thread_ids and limit is not None:
            raise CommandError("--limit can only be used with --all")

        if thread_ids:
            threads = [
                thread
                for thread in (self._lookup_thread(key) for key in thread_ids)
                if thread is not None
            ]
            if not threads:
                raise CommandError("No matching threads found; nothing refreshed")
        elif options["all_threads"]:
            threads = list(self._covered_threads(limit))
        else:
            self.stderr.write(
                self.style.WARNING(
                    "Provide --thread UUID (repeatable) or --all "
                    "to choose what to refresh."
                )
            )
            return

        total = 0
        for thread in threads:
            hits = refresh_thread_hits(thread)
            total += hits
            self.stdout.write(f"{thread.key}: {hits} hits")

        if options["all_threads"] and not thread_ids:
            noun = "thread" if len(threads) == 1 else "threads"
            self.stdout.write(f"Refreshed {len(threads)} {noun}, {total} hits total")

    def _lookup_thread(self, thread_id):
        """Return the thread for ``thread_id``, or warn and return ``None``."""
        try:
            thread = Thread.objects.filter(pk=thread_id).first()
        except (ValidationError, ValueError):
            thread = None
        if thread is None:
            self.stderr.write(
                self.style.WARNING(f"Thread {thread_id} not found; skipped")
            )
        return thread

    def _covered_threads(self, limit):
        """Return threads with a covered subject place, newest first.

        Threads sharing a ``last_seen`` value fall back to descending id so the
        ordering, and therefore ``--limit``, is deterministic.
        """
        queryset = (
            Thread.objects.filter(
                thread_places__role=ThreadPlace.Role.SUBJECT,
                thread_places__place__muni__isnull=False,
            )
            .distinct()
            .order_by("-last_seen", "-id")
        )
        if limit is not None:
            queryset = queryset[:limit]
        return queryset
