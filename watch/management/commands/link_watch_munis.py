"""Link civic.band municipalities to gazetteer places."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watch.muni_link import link_munis


class Command(BaseCommand):
    help = "Link civic.band municipalities to gazetteer places by name and state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report link counts without saving any changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Process at most this many municipalities.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit must be a positive integer")

        with transaction.atomic():
            stats = link_munis(dry_run=options["dry_run"], limit=limit)
            if options["dry_run"]:
                transaction.set_rollback(True)

        summary = " ".join(f"{key}={value}" for key, value in stats.items())
        if options["dry_run"]:
            summary = f"dry-run {summary}"
        self.stdout.write(summary)
