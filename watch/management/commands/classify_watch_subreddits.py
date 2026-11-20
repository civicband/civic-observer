"""Classify watched subreddits through the gazetteer layers."""

from django.core.management.base import BaseCommand, CommandError

from watch.classify import classify_pending_subreddits


class Command(BaseCommand):
    help = "Classify pending subreddits from manual, Wikidata, and gazetteer data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reclassify",
            action="store_true",
            help="Re-run every non-manual row, including already reviewed rows.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Classify at most this many subreddits.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer")

        stats = classify_pending_subreddits(
            reclassify=options["reclassify"],
            limit=limit,
        )
        self.stdout.write(" ".join(f"{key}={value}" for key, value in stats.items()))
