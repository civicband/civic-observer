"""
Management command to index meeting pages in Meilisearch.

This command migrates existing MeetingPage data from PostgreSQL to Meilisearch.
It can index all pages or filter by municipality, date range, etc.

Usage:
    # Index all pages (dry-run first)
    python manage.py index_meeting_pages --dry-run
    python manage.py index_meeting_pages

    # Index specific municipality
    python manage.py index_meeting_pages --municipality alameda-ca

    # Index date range
    python manage.py index_meeting_pages --date-from 2024-01-01 --date-to 2024-12-31

    # Rebuild index (clear and re-index)
    python manage.py index_meeting_pages --rebuild
"""

from datetime import datetime

from django.core.management.base import BaseCommand

from meetings.models import MeetingPage
from municipalities.models import Muni
from searches.indexing import index_queryset_in_batches
from searches.meilisearch_client import delete_all_documents, get_index_stats


class Command(BaseCommand):
    help = "Index meeting pages in Meilisearch"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of pages to index per batch (default: 1000)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be indexed without actually indexing",
        )
        parser.add_argument(
            "--municipality",
            type=str,
            help="Index only pages from this municipality (subdomain)",
        )
        parser.add_argument(
            "--date-from",
            type=str,
            help="Index only pages from meetings on or after this date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--date-to",
            type=str,
            help="Index only pages from meetings on or before this date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Clear the index before indexing (use with caution!)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of pages to index (useful for testing)",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        municipality_subdomain = options.get("municipality")
        date_from_str = options.get("date_from")
        date_to_str = options.get("date_to")
        rebuild = options["rebuild"]
        limit = options.get("limit")

        self.stdout.write(
            self.style.WARNING(
                "=" * 80 + "\nIndex Meeting Pages to Meilisearch\n" + "=" * 80
            )
        )

        # Parse date filters
        date_from = None
        date_to = None
        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Invalid date format for --date-from: "{date_from_str}"\n'
                        "  Use YYYY-MM-DD format\n"
                    )
                )
                return

        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Invalid date format for --date-to: "{date_to_str}"\n'
                        "  Use YYYY-MM-DD format\n"
                    )
                )
                return

        # Build queryset with filters
        queryset = MeetingPage.objects.select_related(
            "document", "document__municipality"
        ).all()

        filters_applied = []

        if municipality_subdomain:
            try:
                municipality = Muni.objects.get(subdomain=municipality_subdomain)
                queryset = queryset.filter(document__municipality=municipality)
                filters_applied.append(f"municipality: {municipality.name}")
            except Muni.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Municipality "{municipality_subdomain}" not found\n'
                    )
                )
                return

        if date_from:
            queryset = queryset.filter(document__meeting_date__gte=date_from)
            filters_applied.append(f"date from: {date_from}")

        if date_to:
            queryset = queryset.filter(document__meeting_date__lte=date_to)
            filters_applied.append(f"date to: {date_to}")

        if limit:
            queryset = queryset[:limit]
            filters_applied.append(f"limit: {limit} pages")

        total_pages = queryset.count()

        # Show what will be indexed
        self.stdout.write(f"\nPages to index: {total_pages:,}")
        if filters_applied:
            self.stdout.write("Filters:")
            for f in filters_applied:
                self.stdout.write(f"  • {f}")
        self.stdout.write("")

        if total_pages == 0:
            self.stdout.write(self.style.WARNING("No pages to index.\n"))
            return

        # Show current index stats
        try:
            stats = get_index_stats("meeting_pages")
            current_docs = getattr(
                stats, "number_of_documents", getattr(stats, "numberOfDocuments", 0)
            )
            self.stdout.write(f"Current index size: {current_docs:,} documents\n")
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not get index stats: {e}\n"))

        # Rebuild warning
        if rebuild:
            self.stdout.write(
                self.style.WARNING(
                    "⚠️  REBUILD MODE: This will DELETE all existing documents first!\n"
                )
            )

        # Dry run
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "[DRY RUN] Would index these pages.\n"
                    "Run without --dry-run to actually index.\n"
                )
            )
            return

        # Confirm before proceeding
        if rebuild:
            confirm = input(
                "⚠️  Are you sure you want to DELETE and rebuild the index? [yes/NO]: "
            )
            if confirm.lower() != "yes":
                self.stdout.write(self.style.WARNING("\nAborted.\n"))
                return
        else:
            confirm = input(f"Index {total_pages:,} pages? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("\nAborted.\n"))
                return

        # Rebuild: delete all documents
        if rebuild:
            self.stdout.write("\nDeleting all documents from index...")
            try:
                task = delete_all_documents("meeting_pages")
                task_uid = getattr(
                    task, "task_uid", getattr(task, "taskUid", "unknown")
                )
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Delete queued (task {task_uid})\n")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Failed to delete documents: {e}\n")
                )
                return

        # Index pages
        self.stdout.write(
            f"\nIndexing {total_pages:,} pages in batches of {batch_size}...\n"
        )

        def progress_callback(current, total):
            percent = (current / total) * 100
            self.stdout.write(f"  Progress: {current:,} / {total:,} ({percent:.1f}%)")

        try:
            result = index_queryset_in_batches(
                queryset, batch_size=batch_size, progress_callback=progress_callback
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n{'=' * 80}\n"
                    f"Indexing Complete!\n"
                    f"  • Total pages: {result['total']:,}\n"
                    f"  • Indexed: {result['indexed']:,}\n"
                    f"  • Batches: {result['batches']}\n"
                    f"  • Batch size: {result['batch_size']}\n"
                    f"\nNote: Meilisearch processes documents asynchronously.\n"
                    f"Large batches may take a few minutes to appear in search.\n"
                    f"Check progress at: {self.style.HTTP_INFO('http://localhost:7700')}\n"
                    f"{'=' * 80}\n"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n✗ Indexing failed: {e}\n"))
            raise
