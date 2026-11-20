"""Load Wikidata subreddit place links from SPARQL or a saved response file."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watch.wikidata import WIKIDATA_ENDPOINT, Fetch, load_wikidata


class Command(BaseCommand):
    help = "Link Wikidata P3984 subreddit statements to local places."

    def add_arguments(self, parser):
        parser.add_argument(
            "--endpoint",
            default=WIKIDATA_ENDPOINT,
            help=f"Wikidata SPARQL endpoint (default: {WIKIDATA_ENDPOINT}).",
        )
        parser.add_argument(
            "--file",
            help="Read a saved SPARQL JSON response instead of querying Wikidata.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report link counts without saving any changes.",
        )

    def handle(self, *args, **options):
        fetch: Fetch | None = None
        if options["file"]:
            path = Path(options["file"])
            if not path.is_file():
                raise CommandError(f"Wikidata response file not found: {path}")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CommandError(
                    f"Cannot read Wikidata response {path}: {exc}"
                ) from exc

            def read_file(endpoint: str) -> dict:
                return payload

            fetch = read_file

        with transaction.atomic():
            stats = load_wikidata(endpoint=options["endpoint"], fetch=fetch)
            if options["dry_run"]:
                transaction.set_rollback(True)

        summary = " ".join(f"{key}={value}" for key, value in stats.items())
        if options["dry_run"]:
            summary = f"dry-run {summary}"
        self.stdout.write(summary)
