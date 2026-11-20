"""Seed manual subreddit labels and hand-written place aliases."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watch.subreddits import (
    ALIASES_CSV,
    SUBREDDITS_CSV,
    seed_aliases,
    seed_subreddits,
)


class Command(BaseCommand):
    help = "Seed manual subreddit labels and hand-written place aliases from CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "--subreddits-csv",
            default=str(SUBREDDITS_CSV),
            help=f"Subreddit label CSV (default: {SUBREDDITS_CSV}).",
        )
        parser.add_argument(
            "--aliases-csv",
            default=str(ALIASES_CSV),
            help=f"Manual alias CSV (default: {ALIASES_CSV}).",
        )
        parser.add_argument(
            "--skip-aliases",
            action="store_true",
            help="Seed subreddit labels without loading manual aliases.",
        )

    def handle(self, *args, **options):
        subreddits_path = Path(options["subreddits_csv"])
        aliases_path = Path(options["aliases_csv"])

        if not subreddits_path.is_file():
            raise CommandError(f"Subreddit CSV not found: {subreddits_path}")
        if not options["skip_aliases"] and not aliases_path.is_file():
            raise CommandError(f"Alias CSV not found: {aliases_path}")

        with transaction.atomic():
            stats = seed_subreddits(subreddits_path)
            aliases = 0 if options["skip_aliases"] else seed_aliases(aliases_path)

        self.stdout.write(
            " ".join(f"{key}={value}" for key, value in stats.items())
            + f" aliases={aliases}"
        )
