"""
Management command to re-backfill municipalities with failed or incomplete data.

This command identifies and queues backfill jobs for:
- Municipalities that were never indexed (last_indexed IS NULL)
- Municipalities with zero pages (indexed but backfill failed)
- Municipalities with suspiciously low page counts (< 100 pages)
"""

import django_rq
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from meetings.tasks import backfill_municipality_meetings_task
from municipalities.models import Muni


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

    def handle(self, *args, **options):
        min_pages = options["min_pages"]
        dry_run = options["dry_run"]
        only_never_indexed = options["only_never_indexed"]
        only_zero_pages = options["only_zero_pages"]

        self.stdout.write(
            self.style.WARNING("=" * 80 + "\nRe-backfill Failed Municipalities\n=" * 80)
        )

        # Get municipalities with page counts
        municipalities = Muni.objects.annotate(
            page_count=Count("meetingdocument__meetingpage")
        )

        # Build query based on options
        if only_never_indexed:
            query = Q(last_indexed__isnull=True)
            description = "never indexed"
        elif only_zero_pages:
            query = Q(last_indexed__isnull=False) & Q(page_count=0)
            description = "indexed but have 0 pages"
        else:
            # Default: all failed/incomplete
            query = Q(last_indexed__isnull=True) | Q(page_count__lt=min_pages)
            description = f"never indexed or have < {min_pages} pages"

        municipalities_to_backfill = municipalities.filter(query).order_by("subdomain")

        total_count = municipalities_to_backfill.count()

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
        never_indexed = municipalities_to_backfill.filter(
            last_indexed__isnull=True
        ).count()
        zero_pages = municipalities_to_backfill.filter(
            last_indexed__isnull=False, page_count=0
        ).count()
        incomplete = municipalities_to_backfill.filter(
            last_indexed__isnull=False, page_count__gt=0, page_count__lt=min_pages
        ).count()

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

        for muni in municipalities_to_backfill:
            try:
                job = queue.enqueue(
                    backfill_municipality_meetings_task,
                    muni.id,
                    job_timeout="30m",  # 30 minute timeout per municipality
                )
                enqueued_count += 1
                self.stdout.write(f"  ✓ {muni.subdomain:30} (job: {job.id[:8]}...)")
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ✗ {muni.subdomain:30} ERROR: {e}")
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
