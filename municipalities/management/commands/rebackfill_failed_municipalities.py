"""
Management command to re-backfill municipalities with failed or incomplete data.

This command identifies and queues backfill jobs for:
- Municipalities that were never indexed (last_indexed IS NULL)
- Municipalities with zero pages (indexed but backfill failed)
- Municipalities with suspiciously low page counts (< 100 pages)
"""

from collections import namedtuple

import django_rq
from django.core.management.base import BaseCommand
from django.db import connection

from meetings.tasks import backfill_municipality_meetings_task

MuniWithCount = namedtuple(
    "MuniWithCount", ["id", "subdomain", "name", "last_indexed", "page_count"]
)


class Command(BaseCommand):
    help = "Re-backfill municipalities with failed or incomplete backfills"

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-pages",
            type=int,
            default=100,
            help="Minimum page count threshold (default: 100). "
            "Municipalities with fewer pages will be re-backfilled.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be backfilled without actually enqueueing jobs",
        )
        parser.add_argument(
            "--only-never-indexed",
            action="store_true",
            help="Only backfill municipalities that were never indexed",
        )
        parser.add_argument(
            "--only-zero-pages",
            action="store_true",
            help="Only backfill municipalities with zero pages",
        )

    def _get_municipalities_with_page_counts(self):
        """Get all municipalities with their page counts using efficient SQL."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    m.id,
                    m.subdomain,
                    m.name,
                    m.last_indexed,
                    COALESCE(COUNT(mp.id), 0) as page_count
                FROM municipalities_muni m
                LEFT JOIN meetings_meetingdocument md ON md.municipality_id = m.id
                LEFT JOIN meetings_meetingpage mp ON mp.document_id = md.id
                GROUP BY m.id, m.subdomain, m.name, m.last_indexed
                ORDER BY m.subdomain
            """)
            rows = cursor.fetchall()
            return [MuniWithCount(*row) for row in rows]

    def handle(self, *args, **options):
        min_pages = options["min_pages"]
        dry_run = options["dry_run"]
        only_never_indexed = options["only_never_indexed"]
        only_zero_pages = options["only_zero_pages"]

        self.stdout.write(
            self.style.WARNING("=" * 80 + "\nRe-backfill Failed Municipalities\n=" * 80)
        )

        # Get municipalities with page counts using efficient SQL
        self.stdout.write("Analyzing municipalities... ")
        all_municipalities = self._get_municipalities_with_page_counts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Found {len(all_municipalities)} total municipalities.\n"
            )
        )

        # Filter based on options
        if only_never_indexed:
            municipalities_to_backfill = [
                m for m in all_municipalities if m.last_indexed is None
            ]
            description = "never indexed"
        elif only_zero_pages:
            municipalities_to_backfill = [
                m
                for m in all_municipalities
                if m.last_indexed is not None and m.page_count == 0
            ]
            description = "indexed but have 0 pages"
        else:
            # Default: all failed/incomplete
            municipalities_to_backfill = [
                m
                for m in all_municipalities
                if m.last_indexed is None or m.page_count < min_pages
            ]
            description = f"never indexed or have < {min_pages} pages"

        total_count = len(municipalities_to_backfill)

        if total_count == 0:
            self.stdout.write(
                self.style.SUCCESS(f"\n✓ No municipalities found that {description}.\n")
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"\nFound {total_count} municipalities that {description}:\n"
            )
        )

        # Show breakdown
        never_indexed = len(
            [m for m in municipalities_to_backfill if m.last_indexed is None]
        )
        zero_pages = len(
            [
                m
                for m in municipalities_to_backfill
                if m.last_indexed is not None and m.page_count == 0
            ]
        )
        incomplete = len(
            [
                m
                for m in municipalities_to_backfill
                if m.last_indexed is not None and 0 < m.page_count < min_pages
            ]
        )

        self.stdout.write(f"  • Never indexed: {never_indexed}")
        self.stdout.write(f"  • Zero pages: {zero_pages}")
        self.stdout.write(f"  • Incomplete (< {min_pages}): {incomplete}")
        self.stdout.write("")

        # Show sample
        self.stdout.write("Sample municipalities:\n")
        for muni in municipalities_to_backfill[:10]:
            status = (
                "never indexed"
                if muni.last_indexed is None
                else f"{muni.page_count} pages"
            )
            self.stdout.write(f"  • {muni.subdomain:30} {muni.name:30} ({status})")

        if total_count > 10:
            self.stdout.write(f"  ... and {total_count - 10} more\n")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\n[DRY RUN] Would enqueue backfill jobs for these municipalities.\n"
                    "Run without --dry-run to actually enqueue jobs.\n"
                )
            )
            return

        # Confirm before proceeding
        self.stdout.write(
            self.style.WARNING(
                f"\nThis will enqueue {total_count} backfill jobs.\n"
                "Each job may take several minutes to complete.\n"
            )
        )

        confirm = input("Continue? [y/N]: ")
        if confirm.lower() != "y":
            self.stdout.write(self.style.WARNING("\nAborted.\n"))
            return

        # Enqueue jobs
        queue = django_rq.get_queue("default")
        enqueued_count = 0
        error_count = 0

        self.stdout.write(self.style.WARNING("\nEnqueueing backfill jobs...\n"))

        for muni_data in municipalities_to_backfill:
            try:
                job = queue.enqueue(
                    backfill_municipality_meetings_task,
                    muni_data.id,
                    job_timeout="30m",  # 30 minute timeout per municipality
                )
                enqueued_count += 1
                self.stdout.write(
                    f"  ✓ {muni_data.subdomain:30} (job: {job.id[:8]}...)"
                )
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ✗ {muni_data.subdomain:30} ERROR: {e}")
                )

        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'=' * 80}\n"
                f"Summary:\n"
                f"  • Enqueued: {enqueued_count}\n"
                f"  • Errors: {error_count}\n"
                f"  • Total: {total_count}\n"
                f"\nMonitor progress at /django-rq/ admin panel.\n"
                f"{'=' * 80}\n"
            )
        )
