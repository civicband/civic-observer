"""Load GeoNames cities into Canadian places and non-US lookup rows."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watch.gazetteer import (
    DEFAULT_MIN_POPULATION,
    download_geonames,
    geonames_path,
    load_geonames,
)
from watch.normalize import ensure_data_dir


class Command(BaseCommand):
    help = (
        "Load GeoNames cities500 data into Canadian places and non-US city lookup rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--download",
            action="store_true",
            help=(
                "Download and extract cities500.zip into WATCH_DATA_DIR. "
                "Files are cached for later runs."
            ),
        )
        parser.add_argument(
            "--source-dir",
            help="Read cities500.txt from this directory instead.",
        )
        parser.add_argument(
            "--min-population",
            type=int,
            default=DEFAULT_MIN_POPULATION,
            help=(
                "Minimum population for non-Canadian non-US lookup cities "
                f"(default: {DEFAULT_MIN_POPULATION})."
            ),
        )

    def handle(self, *args, **options):
        source_dir = options["source_dir"]

        if options["download"] and source_dir:
            raise CommandError("--download cannot be combined with --source-dir")

        if options["download"]:
            directory = ensure_data_dir()
            self.stdout.write(
                f"Downloading GeoNames cities into {directory} (cached)..."
            )
            cities_path = download_geonames(directory)
        else:
            directory = Path(source_dir) if source_dir else ensure_data_dir()
            if not directory.is_dir():
                raise CommandError(f"GeoNames source directory not found: {directory}")
            cities_path = geonames_path(directory)
            if not cities_path.is_file():
                raise CommandError(
                    f"{cities_path.name} not found in {directory}. "
                    "Use --download or --source-dir."
                )
            self.stdout.write(f"Loading GeoNames cities from {cities_path}...")

        with transaction.atomic():
            counts = load_geonames(
                cities_path, min_population=options["min_population"]
            )

        summary = " ".join(f"{key}={value}" for key, value in counts.items())
        self.stdout.write(summary)
