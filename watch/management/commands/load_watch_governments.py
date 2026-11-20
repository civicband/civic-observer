"""Load Census of Governments units into CivicBand Watch places."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from watch.gazetteer import GOVERNMENT_FILENAME, load_governments
from watch.normalize import ensure_data_dir


class Command(BaseCommand):
    help = (
        "Flag Census of Governments units as governments and fill population "
        "and county."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-dir",
            help=(
                f"Directory containing {GOVERNMENT_FILENAME} instead of WATCH_DATA_DIR."
            ),
        )
        parser.add_argument(
            "--file",
            help="Read a specific Census of Governments XLSX file.",
        )

    def handle(self, *args, **options):
        if options["file"] and options["source_dir"]:
            raise CommandError("--file cannot be combined with --source-dir")

        if options["file"]:
            path = Path(options["file"])
        else:
            directory = (
                Path(options["source_dir"])
                if options["source_dir"]
                else ensure_data_dir()
            )
            path = directory / GOVERNMENT_FILENAME

        if not path.is_file():
            raise CommandError(
                f"Census of Governments file not found: {path}. "
                "Use --file or --source-dir."
            )

        stats = load_governments(path)

        summary = " ".join(f"{key}={value}" for key, value in stats.items())
        self.stdout.write(summary)
        if stats["unmatched_school"]:
            self.stderr.write(
                self.style.WARNING(
                    f"Unmatched school districts: {stats['unmatched_school']}"
                )
            )
