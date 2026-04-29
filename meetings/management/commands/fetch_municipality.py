"""
Fetch municipality data from civic.band API or generate test fixtures.

Usage:
    # With real API (requires CORKBOARD_SERVICE_SECRET in .env)
    python manage.py fetch_municipality alameda-ca --backfill

    # With generated test fixtures (no API needed)
    python manage.py fetch_municipality alameda-ca --fixtures
    python manage.py fetch_municipality alameda-ca --fixtures --count 50

    # Generate fixtures and index into Quickwit
    python manage.py fetch_municipality alameda-ca --fixtures --quickwit
"""

import uuid
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from municipalities.models import Muni


class Command(BaseCommand):
    help = "Fetch municipality metadata or generate test fixtures"

    def add_arguments(self, parser):
        parser.add_argument(
            "subdomain",
            type=str,
            help="Municipality subdomain (e.g., alameda-ca)",
        )
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Backfill meeting documents from civic.band API",
        )
        parser.add_argument(
            "--fixtures",
            action="store_true",
            help="Generate realistic test fixture data (no API needed)",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=100,
            help="Number of pages to generate (with --fixtures, default: 100)",
        )
        parser.add_argument(
            "--documents",
            type=int,
            default=5,
            help="Number of meeting documents to create (with --fixtures, default: 5)",
        )
        parser.add_argument(
            "--quickwit",
            action="store_true",
            help="Index generated pages into Quickwit",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing municipality data",
        )

    def handle(self, *args, **options):
        subdomain = options["subdomain"]
        do_backfill = options["backfill"]
        do_fixtures = options["fixtures"]
        do_quickwit = options["quickwit"]
        overwrite = options["overwrite"]
        fixture_count = options["count"]
        fixture_docs = options["documents"]

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"  Municipality: {subdomain}")
        self.stdout.write(f"  Mode: {'fixtures' if do_fixtures else 'api backfill' if do_backfill else 'municipality only'}")
        self.stdout.write(f"{'=' * 60}\n")

        muni = self._ensure_municipality(subdomain, overwrite)

        if do_fixtures:
            from meetings.models import MeetingDocument, MeetingPage

            # Check for existing fixture data
            existing_docs = MeetingDocument.objects.filter(municipality=muni)
            existing_count = MeetingPage.objects.filter(document__in=existing_docs).count()

            if existing_count > 0 and not overwrite:
                self.stdout.write(f"\n  Found {existing_count} existing pages, skipping generation")
                self.stdout.write("  Tip: use --overwrite --fixtures --quickwit to re-index")
            else:
                if existing_count > 0 and overwrite:
                    self.stdout.write(f"\n  Overwriting {existing_count} existing pages...")
                    MeetingPage.objects.filter(document__in=existing_docs).delete()
                    existing_docs.delete()

                self.stdout.write(f"\n  Generating {fixture_count} pages across {fixture_docs} documents...")

                docs = []
                base_date = date.today() - timedelta(days=30)
                meeting_names = [
                    "City Council Regular Meeting",
                    "Planning Commission",
                    "Board of Supervisors",
                    "Budget Committee",
                    "Public Safety Committee",
                ]

                for i in range(fixture_docs):
                    meeting_date = base_date + timedelta(days=i * 7)
                    for doc_type in ("agenda", "minutes"):
                        doc = MeetingDocument.objects.create(
                            municipality_id=muni.id,
                            meeting_name=f"{meeting_names[i % len(meeting_names)]}",
                            meeting_date=meeting_date,
                            document_type=doc_type,
                        )
                        docs.append(doc)

                pages_per_doc = fixture_count // len(docs)
                sample_texts = [
                    "The city council discussed the new police budget allocation for the upcoming fiscal year.",
                    "Planning commission reviewed the zoning changes for downtown development and affordable housing.",
                    "Public safety committee addressed emergency response times and fire department staffing.",
                    "Budget committee approved the quarterly spending report and tax revenue projections.",
                    "City council voted on the new infrastructure bill and road maintenance funding.",
                    "The meeting covered community input on environmental policy and green energy initiatives.",
                    "Discussion focused on public transit expansion and bus route improvements.",
                    "The council reviewed the proposed amendments to the municipal code regarding short-term rentals.",
                    "Public comment period included residents discussing neighborhood safety concerns.",
                    "The committee approved the new park development plan and recreation center funding.",
                    "City staff presented the annual report on water quality and sewage treatment upgrades.",
                    "Mayor discussed the city's response to homelessness and social services expansion.",
                ]

                created = 0
                for doc in docs:
                    for page_num in range(1, pages_per_doc + 1):
                        page_id = str(uuid.uuid4())[:24]
                        text = f"[Page {page_num}] {sample_texts[(page_num - 1) % len(sample_texts)]}"
                        MeetingPage.objects.create(
                            id=page_id,
                            document=doc,
                            page_number=page_num,
                            text=text,
                        )
                        created += 1

                muni.pages = created
                muni.save(update_fields=["pages"])
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created {created} pages across {len(docs)} documents"))

        elif do_backfill:
            self.stdout.write(f"\n  Starting backfill for {muni.subdomain}...")
            try:
                from meetings.services import backfill_municipality_meetings

                stats = backfill_municipality_meetings(muni)
                self.stdout.write(self.style.SUCCESS("\n  ✓ Backfill completed:"))
                self.stdout.write(f"    Documents created: {stats['documents_created']}")
                self.stdout.write(f"    Documents updated: {stats['documents_updated']}")
                self.stdout.write(f"    Pages created:     {stats['pages_created']}")
                self.stdout.write(f"    Pages updated:     {stats['pages_updated']}")
                self.stdout.write(f"    Errors:            {stats['errors']}")
            except ImportError:
                self.stdout.write(self.style.ERROR("\n  ✗ meetings app not available"))
                return
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\n  ✗ Backfill failed: {e}"))
                raise CommandError(f"Backfill failed: {e}") from e

        # Index into Quickwit if requested
        if do_quickwit:
            self.stdout.write("\n  Indexing pages into Quickwit...")
            self.stdout.write("  (Quickwit commit timeout is 10s — results available after ~10s)")
            try:
                from meetings.models import MeetingPage
                from searches.quickwit_client import ingest_documents

                pages = MeetingPage.objects.select_related(
                    "document", "document__municipality"
                ).filter(document__municipality=muni)

                qw_documents: list[dict] = [self._page_to_quickwit_doc(page) for page in pages]

                if qw_documents:
                    result = ingest_documents(qw_documents)
                    if "error" in result:
                        self.stdout.write(self.style.ERROR(f"  ✗ {result['error']}"))
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(f"\n  ✓ Indexed {len(qw_documents)} pages into Quickwit")
                        )
                else:
                    self.stdout.write(self.style.WARNING("\n  No pages to index"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\n  ✗ Quickwit indexing failed: {e}"))
                if not do_fixtures and not do_backfill:
                    raise CommandError(f"Quickwit indexing failed: {e}") from e

    def _ensure_municipality(self, subdomain, overwrite):
        import httpx

        api_url = f"https://{subdomain}.civic.band/"
        metadata_url = f"https://{subdomain}.civic.band/api/metadata.json"

        # Try metadata API
        try:
            resp = httpx.get(metadata_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return self._create_or_update_muni(subdomain, data, overwrite)
        except (httpx.HTTPError, ValueError):
            pass

        # Try scraping homepage
        try:
            resp = httpx.get(api_url, timeout=30)
            if resp.status_code == 200:
                data = self._parse_homepage_metadata(resp.text, subdomain)
                if data:
                    return self._create_or_update_muni(subdomain, data, overwrite)
        except (httpx.HTTPError, ValueError):
            pass

        # Fallback to defaults
        defaults = {
            "name": subdomain.replace("-", " ").title(),
            "state": "CA",
            "country": "US",
            "kind": "City",
            "pages": 0,
        }
        return self._create_or_update_muni(subdomain, defaults, overwrite)

    def _create_or_update_muni(self, subdomain, data, overwrite):
        muni_data = {
            "name": data.get("name", data.get("municipality_name", subdomain)),
            "state": data.get("state", data.get("state_abbr", "CA")),
            "country": data.get("country", data.get("country_code", "US")),
            "kind": data.get("kind", data.get("municipality_kind", "City")),
            "pages": data.get("pages", 0),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        }
        muni_data = {k: v for k, v in muni_data.items() if v is not None}

        try:
            muni, created = Muni.objects.get_or_create(
                subdomain=subdomain, defaults=muni_data
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created municipality: {muni}"))
            elif overwrite:
                for key, value in muni_data.items():
                    setattr(muni, key, value)
                muni.save()
                self.stdout.write(self.style.SUCCESS(f"  ✓ Updated municipality: {muni}"))
            else:
                self.stdout.write(f"  ✓ Municipality already exists: {muni}")
            return muni
        except Muni.DoesNotExist:
            muni = Muni.objects.create(subdomain=subdomain, **muni_data)
            self.stdout.write(self.style.SUCCESS(f"  ✓ Created municipality: {muni}"))
            return muni

    def _parse_homepage_metadata(self, html, subdomain):
        import re
        data = {}
        name_match = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
            html,
        )
        if name_match:
            data["name"] = name_match.group(1)
        if "name" not in data:
            data["name"] = subdomain.replace("-", " ").title()
        return data

    def _page_to_quickwit_doc(self, page):
        """Convert a MeetingPage to a Quickwit-compatible document dict."""
        md = page.document.meeting_date
        meeting_date_str = f"{md.isoformat()}T00:00:00"

        return {
            "id": page.id,
            "page_number": page.page_number,
            "text": page.text,
            "page_image": page.page_image,
            "meeting_name": page.document.meeting_name,
            "meeting_date": meeting_date_str,
            "document_type": page.document.document_type,
            "municipality_id": str(page.document.municipality.id),
            "municipality_subdomain": page.document.municipality.subdomain,
            "municipality_name": page.document.municipality.name,
            "state": page.document.municipality.state,
            "document_id": str(page.document.id),
        }
