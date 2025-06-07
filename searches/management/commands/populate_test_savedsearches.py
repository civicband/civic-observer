import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from searches.models import SavedSearch, Search

User = get_user_model()


class Command(BaseCommand):
    help = "Populate test saved search data linked to existing searches"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10,
            help="Number of test saved searches to create (default: 10)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing saved search data before creating new data",
        )
        parser.add_argument(
            "--create-users",
            action="store_true",
            help="Create test users if they don't exist",
        )

    def handle(self, *args, **options):
        count = options["count"]

        if options["clear"]:
            self.stdout.write("Clearing existing saved search data...")
            SavedSearch.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Cleared existing saved search data"))

        # Ensure we have some searches to work with
        searches = list(Search.objects.all())
        if not searches:
            self.stdout.write(
                self.style.ERROR(
                    "No Search objects found. Run 'populate_test_searches' first."
                )
            )
            return

        # Get or create test users
        users = self.get_or_create_test_users(create_new=options["create_users"])
        if not users:
            self.stdout.write(
                self.style.ERROR(
                    "No users found. Use --create-users flag or create users manually."
                )
            )
            return

        # Sample saved search names
        search_name_templates = [
            "{search_type} in {muni}",
            "Monitor {search_type} - {muni}",
            "{muni} {search_type} Watch",
            "Weekly {search_type} Updates",
            "{search_type} Notifications",
            "Track {search_type} Changes",
            "{muni} Municipal {search_type}",
        ]

        created_count = 0

        for _ in range(count):
            # Randomly select a user and search
            user = random.choice(users)
            search = random.choice(searches)

            # Generate a descriptive name
            if search.search_term:
                search_type = f'"{search.search_term}"'
            else:
                search_type = "All Results"

            name_template = random.choice(search_name_templates)
            name = name_template.format(
                search_type=search_type,
                muni=search.muni.name,
            )

            # Create saved search (avoid duplicates with get_or_create)
            saved_search, created = SavedSearch.objects.get_or_create(
                user=user,
                search=search,
                defaults={"name": name},
            )

            if created:
                created_count += 1
                self.stdout.write(f"Created saved search: {saved_search}")

                # Randomly set some notification timestamps to simulate activity
                if random.choice([True, False]):
                    # Simulate past notifications
                    days_ago = random.randint(1, 30)
                    saved_search.last_notification_sent = timezone.now() - timedelta(
                        days=days_ago
                    )
                    saved_search.save(update_fields=["last_notification_sent"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {created_count} test saved searches"
            )
        )

    def get_or_create_test_users(self, create_new=False):
        """Get existing users or create test users if requested."""
        existing_users = list(User.objects.all())

        if existing_users and not create_new:
            self.stdout.write(f"Using {len(existing_users)} existing users")
            return existing_users

        if create_new:
            test_users_data = [
                {
                    "email": "alice@example.com",
                    "first_name": "Alice",
                    "last_name": "Johnson",
                },
                {
                    "email": "bob@example.com",
                    "first_name": "Bob",
                    "last_name": "Smith",
                },
                {
                    "email": "carol@example.com",
                    "first_name": "Carol",
                    "last_name": "Williams",
                },
                {
                    "email": "david@example.com",
                    "first_name": "David",
                    "last_name": "Brown",
                },
                {
                    "email": "eve@example.com",
                    "first_name": "Eve",
                    "last_name": "Davis",
                },
            ]

            users = []
            for user_data in test_users_data:
                user, created = User.objects.get_or_create(
                    email=user_data["email"],
                    defaults={
                        "username": user_data["email"],
                        "first_name": user_data["first_name"],
                        "last_name": user_data["last_name"],
                        "is_active": True,
                    },
                )
                users.append(user)
                if created:
                    self.stdout.write(f"Created user: {user.email}")  # type: ignore

            return users + existing_users

        return existing_users
