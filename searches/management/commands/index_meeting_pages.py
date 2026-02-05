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

        # Show current index stats
        try:
            stats = get_index_stats("meeting_pages")
            current_docs = stats.number_of_documents
            self.stdout.write(f"\nCurrent index size: {current_docs:,} documents\n")
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not get index stats: {e}\n"))

        # Rebuild: delete all documents first
        if rebuild:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  REBUILD MODE: This will DELETE all existing documents first!\n"
                )
            )
            if not dry_run:
                confirm = input(
                    "⚠️  Are you sure you want to DELETE and rebuild the index? [yes/NO]: "
                )
                if confirm.lower() != "yes":
                    self.stdout.write(self.style.WARNING("\nAborted.\n"))
                    return

                self.stdout.write("\nDeleting all documents from index...")
                try:
                    task = delete_all_documents("meeting_pages")
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Delete queued (task {task.task_uid})\n"
                        )
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ Failed to delete documents: {e}\n")
                    )
                    return

        # If specific municipality requested, process just that one
        if municipality_subdomain:
            try:
                municipality = Muni.objects.get(subdomain=municipality_subdomain)
                municipalities = [municipality]
            except Muni.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Municipality "{municipality_subdomain}" not found\n'
                    )
                )
                return
        else:
            # Process all municipalities one at a time
            municipalities = list(
                Muni.objects.filter(meetings__pages__isnull=False)
                .distinct()
                .order_by("name")
            )

        self.stdout.write(
            f"\nWill process {len(municipalities)} municipalities one at a time\n"
        )

        # Process each municipality
        total_indexed = 0
        total_batches = 0
        failed_municipalities = []

        for idx, municipality in enumerate(municipalities, 1):
            self.stdout.write(
                f"\n[{idx}/{len(municipalities)}] Processing: {municipality.name} ({municipality.subdomain})"
            )

            # Build queryset for this municipality
            queryset = MeetingPage.objects.filter(
                document__municipality=municipality
            ).select_related("document", "document__municipality")

            if date_from:
                queryset = queryset.filter(document__meeting_date__gte=date_from)
            if date_to:
                queryset = queryset.filter(document__meeting_date__lte=date_to)
            if limit:
                queryset = queryset[:limit]

            muni_total = queryset.count()
            self.stdout.write(f"  Pages: {muni_total:,}")

            if muni_total == 0:
                self.stdout.write("  Skipping (no pages)")
                continue

            if dry_run:
                self.stdout.write("  [DRY RUN] Would index these pages")
                continue

            # Index this municipality
            def progress_callback(current, total):
                percent = (current / total) * 100
                self.stdout.write(
                    f"    Progress: {current:,} / {total:,} ({percent:.1f}%)"
                )

            try:
                result = index_queryset_in_batches(
                    queryset,
                    batch_size=batch_size,
                    progress_callback=progress_callback,
                )

                total_indexed += result["indexed"]
                total_batches += result["batches"]

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Indexed {result['indexed']:,} pages in {result['batches']} batches"
                    )
                )

            except Exception as e:
                failed_municipalities.append((municipality, str(e)))
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Failed to index {municipality.name}: {e}")
                )
                # Continue to next municipality instead of crashing

        # Summary
        self.stdout.write(
            self.style.WARNING(f"\n{'=' * 80}\nIndexing Summary\n{'=' * 80}\n")
        )

        if dry_run:
            self.stdout.write(
                "[DRY RUN] No pages were actually indexed.\n"
                "Run without --dry-run to actually index.\n"
            )
        else:
            self.stdout.write(f"Municipalities processed: {len(municipalities)}")
            self.stdout.write(f"Total pages indexed: {total_indexed:,}")
            self.stdout.write(f"Total batches: {total_batches}")

            if failed_municipalities:
                self.stdout.write(
                    self.style.ERROR(
                        f"\n⚠️  {len(failed_municipalities)} municipalities failed:"
                    )
                )
                for muni, error in failed_municipalities:
                    self.stdout.write(f"  • {muni.name}: {error}")
            else:
                self.stdout.write(
                    self.style.SUCCESS("\n✓ All municipalities indexed successfully!")
                )

            self.stdout.write(
                f"\nNote: Meilisearch processes documents asynchronously.\n"
                f"Check progress at: {self.style.HTTP_INFO('http://localhost:7700')}\n"
            )

        self.stdout.write("=" * 80 + "\n")
