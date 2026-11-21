"""Suggest subreddit places from recent subject thread evidence."""

from django.core.management.base import BaseCommand, CommandError

from watch.evidence import (
    DEFAULT_DAYS,
    DEFAULT_MIN_THREADS,
    DEFAULT_SHARE,
    suggest_subreddits,
)


class Command(BaseCommand):
    help = (
        "Suggest subreddit places from recent subject thread evidence. "
        "Manual labels are reported as conflicts and never overwritten."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help=f"Look back this many days by Thread.last_seen (default {DEFAULT_DAYS}).",
        )
        parser.add_argument(
            "--min-threads",
            type=int,
            default=DEFAULT_MIN_THREADS,
            dest="min_threads",
            help=(
                "Require the leading place to appear in at least this many "
                f"subject threads (default {DEFAULT_MIN_THREADS})."
            ),
        )
        parser.add_argument(
            "--share",
            type=float,
            default=DEFAULT_SHARE,
            help=(
                "Require the leading place to cover at least this share of the "
                f"subreddit's subject threads, between 0 and 1 "
                f"(default {DEFAULT_SHARE})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Report what would change without writing suggestions.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        min_threads = options["min_threads"]
        share = options["share"]
        if days < 1:
            raise CommandError("--days must be a positive integer")
        if min_threads < 1:
            raise CommandError("--min-threads must be a positive integer")
        if not 0 <= share <= 1:
            raise CommandError("--share must be between 0 and 1")

        stats = suggest_subreddits(
            days=days,
            min_threads=min_threads,
            share=share,
            dry_run=options["dry_run"],
        )
        prefix = "dry run: " if options["dry_run"] else ""
        self.stdout.write(
            prefix + " ".join(f"{key}={value}" for key, value in stats.items())
        )
