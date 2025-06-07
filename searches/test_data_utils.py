"""
Utility functions for generating test data for searches.

This module provides shared functionality for creating realistic test data
for Search and SavedSearch models, used by both admin actions and
management commands.
"""

import random
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class TestDataGenerator:
    """Generator for realistic test data for searches."""

    MEETING_TYPES = [
        "City Council",
        "Planning Commission",
        "Park Board",
        "School Board",
        "Water Board",
    ]

    SEARCH_NAME_TEMPLATES = [
        "{search_type} in {muni}",
        "Monitor {search_type} - {muni}",
        "{muni} {search_type} Watch",
        "Weekly {search_type} Updates",
        "{search_type} Notifications",
        "Track {search_type} Changes",
        "{muni} Municipal {search_type}",
    ]

    @classmethod
    def populate_search_with_test_data(cls, search):
        """Populate a search with realistic test data."""
        now = timezone.now()

        # Generate agenda matches
        agenda_matches = cls._generate_agenda_matches(search, now)

        # Generate minutes matches
        minutes_matches = cls._generate_minutes_matches(search, now)

        # Update the search with results
        search.agenda_match_json = agenda_matches if agenda_matches else None
        search.minutes_match_json = minutes_matches if minutes_matches else None
        search.last_fetched = now

        if agenda_matches:
            search.last_agenda_matched = now
        if minutes_matches:
            search.last_minutes_matched = now

        search.save()

    @classmethod
    def _generate_agenda_matches(
        cls, search, now: timezone.datetime
    ) -> list[dict[str, Any]]:  # type: ignore
        """Generate agenda match data for a search."""
        agenda_matches = []
        agenda_count = random.randint(0, 15)

        for i in range(agenda_count):
            # Generate dates in the future for agendas
            future_date = now + timedelta(days=random.randint(1, 90))
            date_str = future_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                agenda_matches.append(
                    {
                        "meeting": random.choice(cls.MEETING_TYPES),
                        "date": date_str,
                        "count(page)": random.randint(1, 10),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(cls.MEETING_TYPES)
                agenda_matches.append(
                    {
                        "id": f"agenda-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 50),
                        "text": cls._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/page-{i}.png",
                    }
                )

        return agenda_matches

    @classmethod
    def _generate_minutes_matches(
        cls, search, now: timezone.datetime
    ) -> list[dict[str, Any]]:  # type: ignore
        """Generate minutes match data for a search."""
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
                        "meeting": random.choice(cls.MEETING_TYPES),
                        "date": date_str,
                        "count(page)": random.randint(1, 15),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(cls.MEETING_TYPES)
                minutes_matches.append(
                    {
                        "id": f"minutes-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 75),
                        "text": cls._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/minutes-page-{i}.png",
                    }
                )

        return minutes_matches

    @classmethod
    def _generate_realistic_text(
        cls, search_term: str | None, meeting_name: str
    ) -> str:
        """Generate realistic meeting text that contains the search term."""
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

        return random.choice(templates)

    @classmethod
    def generate_saved_search_name(cls, search) -> str:
        """Generate a descriptive name for a saved search."""
        if search.search_term:
            search_type = f'"{search.search_term}"'
        else:
            search_type = "All Results"

        name_template = random.choice(cls.SEARCH_NAME_TEMPLATES)
        return name_template.format(
            search_type=search_type,
            muni=search.muni.name,
        )

    @classmethod
    def create_test_saved_searches(cls, count: int = 10) -> int:
        """Create test saved searches and return the number created."""
        from .models import SavedSearch, Search

        # Get available users and searches
        users = list(User.objects.all())
        searches = list(Search.objects.all())

        if not users or not searches:
            return 0

        created_count = 0

        for _ in range(count):
            user = random.choice(users)
            search = random.choice(searches)
            name = cls.generate_saved_search_name(search)

            # Create saved search (avoid duplicates with get_or_create)
            saved_search, created = SavedSearch.objects.get_or_create(
                user=user,
                search=search,
                defaults={"name": name},
            )

            if created:
                created_count += 1

                # Randomly set some notification timestamps to simulate activity
                if random.choice([True, False]):
                    # Simulate past notifications
                    days_ago = random.randint(1, 30)
                    saved_search.last_notification_sent = timezone.now() - timedelta(
                        days=days_ago
                    )
                    saved_search.save(update_fields=["last_notification_sent"])

        return created_count

    @classmethod
    def clear_search_results(cls, search):
        """Clear all results from a search."""
        search.agenda_match_json = None
        search.minutes_match_json = None
        search.last_agenda_matched = None
        search.last_minutes_matched = None
        search.save()
