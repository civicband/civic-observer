from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from searches.models import SavedSearch, Search
from searches.test_data_utils import TestDataGenerator

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

        created_count = TestDataGenerator.create_test_saved_searches(count)

        # Get the created saved searches to show them
        recent_saved_searches = SavedSearch.objects.order_by("-created")[:created_count]
        for saved_search in recent_saved_searches:
            self.stdout.write(f"Created saved search: {saved_search}")

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
