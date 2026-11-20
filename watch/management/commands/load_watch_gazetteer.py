"""Load Census Gazetteer files into CivicBand Watch places."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watch.gazetteer import (
    GAZETTEER_STEMS,
    download_gazetteer,
    load_gazetteer,
    missing_stems,
)
from watch.normalize import ensure_data_dir


class _DryRunRollback(Exception):
    """Raised inside the dry-run transaction to undo loader writes."""


class Command(BaseCommand):
    help = "Load Census Gazetteer files into typed places and aliases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--download",
            action="store_true",
            help=(
                "Download and extract the Gazetteer zips into WATCH_DATA_DIR. "
                "Files are cached even with --dry-run."
            ),
        )
        parser.add_argument(
            "--source-dir",
            help="Read Gazetteer TSV files from this directory instead.",
        )
        parser.add_argument("--year", type=int, default=2026)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Load into a transaction and roll it back.",
        )

    def handle(self, *args, **options):
        year = options["year"]
        source_dir = options["source_dir"]

        if options["download"] and source_dir:
            raise CommandError("--download cannot be combined with --source-dir")

        if options["download"]:
            directory = ensure_data_dir()
            self.stdout.write(
                f"Downloading {year} Gazetteer files into {directory} "
                "(the cache is populated even with --dry-run)..."
            )
            download_gazetteer(year=year, directory=directory)
            source_dir = directory
        elif not source_dir:
            source_dir = ensure_data_dir()

        directory = Path(source_dir)
        if not directory.is_dir():
            raise CommandError(f"Gazetteer source directory not found: {directory}")

        missing = missing_stems(directory, year=year)
        if len(missing) == len(GAZETTEER_STEMS):
            raise CommandError(
                f"No Gazetteer TSV files found in {directory}. "
                "Use --download or --source-dir."
            )
        if missing:
            self.stderr.write(
                self.style.WARNING(
                    "Missing Gazetteer files: " + ", ".join(sorted(missing))
                )
            )

        dry_run = options["dry_run"]
        try:
            with transaction.atomic():
                counts = load_gazetteer(directory, year=year)
                if dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            pass

        summary = " ".join(f"{key}={value}" for key, value in counts.items())
        prefix = "Dry run: " if dry_run else ""
        self.stdout.write(f"{prefix}{summary}")
