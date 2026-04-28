"""Index meeting pages into Quickwit.

Quickwit stores its data on S3-compatible object storage (Fastly).
This command bulk-indexes existing MeetingPage data from PostgreSQL
into Quickwit using the NDJSON ingest API.

Usage:
    # Index all pages
    python manage.py index_meeting_pages_quickwit

    # Index specific municipality
    python manage.py index_meeting_pages_quickwit --municipality alameda-ca

    # Batch size control
    python manage.py index_meeting_pages_quickwit --batch-size 5000

    # Dry run to see what would be indexed
    python manage.py index_meeting_pages_quickwit --dry-run
"""

import json
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from meetings.models import MeetingPage
from municipalities.models import Muni

logger = logging.getLogger(__name__)


def get_quickwit_config():
    """Get Quickwit connection settings."""
    url = getattr(settings, "QUICKWIT_URL", "http://quickwit:7280/api/v1")
    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")
    timeout = getattr(settings, "QUICKWIT_TIMEOUT", 30)
    return url, index_id, timeout


def page_to_document(page) -> dict:
    """Convert a MeetingPage to a Quickwit-compatible document."""
    return {
        "id": page.id,
        "page_number": page.page_number,
        "text": page.text,
        "page_image": page.page_image,
        "document_id": str(page.document.id),
        "meeting_name": page.document.meeting_name,
        "meeting_date": page.document.meeting_date.isoformat(),
        "document_type": page.document.document_type,
        "municipality_id": str(page.document.municipality.id),
        "municipality_subdomain": page.document.municipality.subdomain,
        "municipality_name": page.document.municipality.name,
        "state": page.document.municipality.state,
    }


def ingest_batch(documents: list[dict], url: str, index_id: str, timeout: int) -> dict:
    """Ingest a batch of documents into Quickwit using NDJSON format."""
    if not documents:
        return {"success": True, "count": 0}

    try:
        ingest_url = f"{url}/{index_id}/ingest"
        # Quickwit expects NDJSON (newline-delimited JSON)
        body = "\n".join(json.dumps(doc, default=str) for doc in documents)
        response = requests.post(
            ingest_url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=timeout,
        )
        response.raise_for_status()
        # Quickwit 0.8 returns 202 with {"numDocs": N, "numBytes": M}
        return {"success": True, "count": len(documents), "response": response.json()}
    except requests.RequestException as e:
        logger.error(f"Quickwit ingest failed: {e}")
        return {"success": False, "count": 0, "error": str(e)}


class Command(BaseCommand):
    help = "Index meeting pages into Quickwit on S3 storage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of pages to index per batch (default: 5000)",
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
        limit = options.get("limit")

        qw_url, qw_index_id, qw_timeout = get_quickwit_config()

        self.stdout.write(
            self.style.WARNING(
                "=" * 80
                + f"\nIndex Meeting Pages to Quickwit (S3: Fastly Object Storage)\n"
                + "=" * 80
                + f"\nQuickwit URL: {qw_url}"
                + f"\nIndex ID: {qw_index_id}\n"
            )
        )

        # Parse date filters
        date_from = None
        date_to = None
        if date_from_str:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        if date_to_str:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()

        # Build queryset
        queryset = MeetingPage.objects.select_related(
            "document", "document__municipality"
        ).all()

        if municipality_subdomain:
            try:
                municipality = Muni.objects.get(subdomain=municipality_subdomain)
                queryset = queryset.filter(document__municipality=municipality)
            except Muni.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Municipality "{municipality_subdomain}" not found\n'
                    )
                )
                return
            self.stdout.write(f"Filtering by municipality: {municipality.name}")

        if date_from:
            queryset = queryset.filter(document__meeting_date__gte=date_from)
            self.stdout.write(f"Date from: {date_from}")
        if date_to:
            queryset = queryset.filter(document__meeting_date__lte=date_to)
            self.stdout.write(f"Date to: {date_to}")

        total = queryset.count()
        self.stdout.write(f"\nTotal pages to index: {total:,}")

        if total == 0:
            self.stdout.write(self.style.WARNING("No pages found matching criteria."))
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[DRY RUN] Would index {total:,} pages in batches of {batch_size:,}"
                )
            )
            return

        # Index in batches
        indexed = 0
        failed_batches = 0
        last_pk = ""  # MeetingPage uses CharField as primary key

        self.stdout.write("\nIndexing pages...")

        while True:
            # Get next batch using keyset pagination
            batch_values = list(
                queryset.filter(pk__gt=last_pk)
                .order_by("pk")
                .values(
                    "id",
                    "page_number",
                    "text",
                    "page_image",
                    "document__id",
                    "document__meeting_name",
                    "document__meeting_date",
                    "document__document_type",
                    "document__municipality__id",
                    "document__municipality__subdomain",
                    "document__municipality__name",
                    "document__municipality__state",
                )[:batch_size]
            )

            if not batch_values:
                break

            # Convert to Quickwit documents
            documents = [
                {
                    "id": row["id"],
                    "page_number": row["page_number"],
                    "text": row["text"],
                    "page_image": row["page_image"],
                    "document_id": str(row["document__id"]),
                    "meeting_name": row["document__meeting_name"],
                    "meeting_date": row["document__meeting_date"].isoformat(),
                    "document_type": row["document__document_type"],
                    "municipality_id": str(row["document__municipality__id"]),
                    "municipality_subdomain": row["document__municipality__subdomain"],
                    "municipality_name": row["document__municipality__name"],
                    "state": row["document__municipality__state"],
                }
                for row in batch_values
            ]

            last_pk = batch_values[-1]["id"]

            # Ingest batch
            result = ingest_batch(documents, qw_url, qw_index_id, qw_timeout)

            if result["success"]:
                indexed += result["count"]
                pct = (indexed / total) * 100
                self.stdout.write(
                    f"  ✓ Batch: {indexed:,} / {total:,} ({pct:.1f}%)"
                )
            else:
                failed_batches += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ Batch failed: {result.get('error', 'unknown')}"
                    )
                )

        # Summary
        self.stdout.write(self.style.WARNING(f"\n{'=' * 80}"))
        self.stdout.write(f"Total indexed: {indexed:,}")
        self.stdout.write(f"Total batches: {indexed // batch_size + (1 if indexed % batch_size else 0)}")
        if failed_batches:
            self.stdout.write(
                self.style.ERROR(f"Failed batches: {failed_batches}")
            )
        self.stdout.write(f"Total pages in database: {total:,}")
        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Indexing complete! {indexed:,} pages indexed into Quickwit."
            )
        )
        self.stdout.write("\nNote: Quickwit processes indexes into 'splits' on S3.")
        self.stdout.write("      Search availability: after the next split commit.")
        self.stdout.write("=" * 80 + "\n")
