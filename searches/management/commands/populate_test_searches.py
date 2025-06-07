import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from municipalities.models import Muni
from searches.models import Search


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
            self.populate_search_results(search, search_term)

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

    def populate_search_results(self, search, search_term):
        """Populate a search with realistic test data."""
        now = timezone.now()

        # Generate agenda matches
        agenda_matches = []
        agenda_count = random.randint(0, 15)

        meeting_types = [
            "City Council",
            "Planning Commission",
            "Park Board",
            "School Board",
            "Water Board",
        ]

        for i in range(agenda_count):
            # Generate dates in the future for agendas
            future_date = now + timedelta(days=random.randint(1, 90))
            date_str = future_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                agenda_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 10),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                agenda_matches.append(
                    {
                        "id": f"agenda-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 50),
                        "text": self.generate_realistic_text(search_term, meeting_name),
                        "page_image": f"https://example.com/page-{i}.png",
                    }
                )

        # Generate minutes matches
        minutes_matches = []
        minutes_count = random.randint(0, 12)

        for i in range(minutes_count):
            # Generate dates in the past for minutes
            past_date = now - timedelta(days=random.randint(1, 180))
            date_str = past_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                minutes_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 15),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                minutes_matches.append(
                    {
                        "id": f"minutes-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 75),
                        "text": self.generate_realistic_text(search_term, meeting_name),
                        "page_image": f"https://example.com/minutes-page-{i}.png",
                    }
                )

        # Update the search with results
        search.agenda_match_json = agenda_matches if agenda_matches else None
        search.minutes_match_json = minutes_matches if minutes_matches else None
        search.last_fetched = now

        if agenda_matches:
            search.last_agenda_matched = now
        if minutes_matches:
            search.last_minutes_matched = now

        search.save()

        self.stdout.write(
            f"  -> Added {len(agenda_matches)} agenda matches and {len(minutes_matches)} minutes matches"
        )

    def generate_realistic_text(self, search_term, meeting_name):
        """Generate realistic meeting text that contains the search term."""
        templates = [
            f"The committee discussed the {search_term} proposal submitted by staff members.",
            f"Motion to approve the {search_term} allocation for the upcoming fiscal year.",
            f"Public comment regarding the {search_term} project raised several concerns.",
            f"Staff recommendation on {search_term} was presented to the {meeting_name}.",
            f"The {search_term} initiative will be reviewed at the next meeting.",
            f"Council member Smith raised questions about the {search_term} implementation.",
            f"The {search_term} ordinance was passed with a 4-1 vote.",
            f"Discussion of {search_term} timeline and budget constraints.",
            f"Community feedback on the {search_term} proposal was overwhelmingly positive.",
            f"The {search_term} committee will report back next month.",
        ]

        if not search_term:
            # For "all results" searches, generate generic meeting text
            generic_templates = [
                f"The {meeting_name} convened to discuss various municipal matters.",
                f"Regular business was conducted by the {meeting_name}.",
                "Several agenda items were addressed during the meeting.",
                "Public participation was encouraged during the session.",
                "The meeting concluded with administrative updates.",
            ]
            return random.choice(generic_templates)

        return random.choice(templates)
