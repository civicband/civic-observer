import random

from django.core.management.base import BaseCommand

from municipalities.models import Muni
from searches.models import Search
from searches.test_data_utils import TestDataGenerator


class Command(BaseCommand):
    help = "Populate test search data with simulated agenda and minutes results"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=5,
            help="Number of test searches to create (default: 5)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing search data before creating new data",
        )

    def handle(self, *args, **options):
        count = options["count"]

        if options["clear"]:
            self.stdout.write("Clearing existing search data...")
            Search.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Cleared existing search data"))

        # Get or create some test municipalities
        test_munis = self.get_or_create_test_munis()

        # Sample search terms
        search_terms = [
            "budget",
            "development",
            "zoning",
            "traffic",
            "parks",
            "housing",
            "infrastructure",
            "climate",
            "transportation",
            "emergency",
        ]

        created_count = 0

        for _ in range(count):
            # Randomly choose between keyword search and all results
            is_all_results = random.choice([True, False])
            search_term = "" if is_all_results else random.choice(search_terms)
            muni = random.choice(test_munis)

            # Create or get search
            search, created = Search.objects.get_or_create(
                muni=muni,
                search_term=search_term,
                all_results=is_all_results,
            )

            if created:
                created_count += 1
                self.stdout.write(f"Created search: {search}")

            # Populate with test data
            TestDataGenerator.populate_search_with_test_data(search)

            agenda_count = (
                len(search.agenda_match_json) if search.agenda_match_json else 0
            )
            minutes_count = (
                len(search.minutes_match_json) if search.minutes_match_json else 0
            )
            self.stdout.write(
                f"  -> Added {agenda_count} agenda matches and {minutes_count} minutes matches"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {created_count} test searches with simulated results"
            )
        )

    def get_or_create_test_munis(self):
        """Get or create test municipalities for testing."""
        test_munis_data = [
            {
                "subdomain": "sf",
                "name": "San Francisco",
                "state": "CA",
                "kind": "City",
            },
            {
                "subdomain": "oakland",
                "name": "Oakland",
                "state": "CA",
                "kind": "City",
            },
            {
                "subdomain": "berkeley",
                "name": "Berkeley",
                "state": "CA",
                "kind": "City",
            },
            {
                "subdomain": "palo-alto",
                "name": "Palo Alto",
                "state": "CA",
                "kind": "City",
            },
        ]

        munis = []
        for muni_data in test_munis_data:
            muni, created = Muni.objects.get_or_create(
                subdomain=muni_data["subdomain"],
                defaults=muni_data,
            )
            munis.append(muni)
            if created:
                self.stdout.write(f"Created municipality: {muni}")

        return munis
