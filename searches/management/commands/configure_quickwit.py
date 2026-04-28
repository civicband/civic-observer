"""Configure the Quickwit index for meeting pages.

This command creates the Quickwit index with the proper schema
for searching meeting pages. The index is stored on S3 (Fastly
Object Storage).

Usage:
    python manage.py configure_quickwit                   # Create index
    python manage.py configure_quickwit --dry-run         # Preview
    python manage.py configure_quickwit --reset           # Delete + recreate
"""

import os
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the Quickwit index for meeting pages on S3 storage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without executing",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete and recreate the index (requires confirmation)",
        )
        parser.add_argument(
            "--config",
            type=str,
            default="quickwit/index-config.yaml",
            help="Path to the Quickwit index config YAML",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        reset = options["reset"]
        config_path = options["config"]

        self.stdout.write(
            self.style.WARNING("=" * 80 + "\nQuickwit Index Configuration\n" + "=" * 80)
        )

        url = getattr(settings, "QUICKWIT_URL", "http://quickwit:7280/api/v1")
        index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")

        self.stdout.write(f"  QUICKWIT_URL:    {url}")
        self.stdout.write(f"  QUICKWIT_INDEX:  {index_id}")
        self.stdout.write(f"  Config file:     {config_path}")

        if reset:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  RESET MODE: This will DELETE the entire index!"
                )
            )
            if not dry_run:
                confirm = input("⚠️  Are you sure? [yes/NO]: ")
                if confirm.lower() != "yes":
                    self.stdout.write(self.style.WARNING("\nAborted.\n"))
                    return

                self.stdout.write("\nDeleting existing Quickwit index...")
                result = self._run_quickwit_cli(
                    ["index", "delete", "--index-id", index_id, "--yes"],
                    dry_run=False,
                )
                if result == 0:
                    self.stdout.write(self.style.SUCCESS("  ✓ Index deleted\n"))
                else:
                    self.stdout.write(self.style.ERROR("  ✗ Failed to delete index\n"))
                    return

        self.stdout.write(f"\nCreating Quickwit index from config: {config_path}")
        if dry_run:
            self.stdout.write(
                f"  [DRY RUN] Would run: quickwit index create --index-config {config_path}"
            )
            config_exists = os.path.exists(config_path)
            self.stdout.write(f"  Config file exists: {config_exists}")
            return

        rc = self._run_quickwit_cli(
            ["index", "create", "--index-config", config_path],
            dry_run=False,
        )
        if rc == 0:
            self.stdout.write(self.style.SUCCESS("\n  ✓ Index created successfully"))
        else:
            self.stdout.write(
                self.style.ERROR(f"\n  ✗ Failed to create index (exit {rc})")
            )

    def _run_quickwit_cli(self, args: list[str], dry_run: bool) -> int:
        """Run a quickwit CLI subcommand and return the exit code."""
        if dry_run:
            self.stdout.write(f"  [DRY RUN] quickwit {' '.join(args)}")
            return 0

        try:
            result = subprocess.run(
                ["quickwit", *args],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.stdout.strip():
                self.stdout.write(result.stdout.strip())
            if result.returncode != 0 and result.stderr.strip():
                self.stdout.write(self.style.ERROR(result.stderr.strip()))
            return result.returncode
        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    "\n  The quickwit CLI is not installed on this machine."
                    "\n  Quickwit creates indexes via CLI only (not REST API)."
                    "\n\n  Inside Docker, run:"
                    "\n    docker-compose exec quickwit quickwit index create"
                    " --index-config /opt/quickwit/index-config.yaml"
                    "\n\n  For now, the index config file is available at:"
                    "\n    quickwit/index-config.yaml"
                )
            )
            return 1
        except subprocess.TimeoutExpired:
            self.stdout.write(self.style.ERROR("\n  ✗ quickwit CLI timed out"))
            return 1
