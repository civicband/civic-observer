"""
Management command to identify and repair searches with missing municipalities.

This command helps fix searches that were created before migration 0006 which
changed the municipality field from a ForeignKey to a ManyToMany relationship.
Searches affected by this migration have empty municipalities and will match
all pages instead of filtering to a specific municipality.

Usage:
    # List all broken searches (default)
    python manage.py repair_searches

    # Fix a specific search by adding a municipality
    python manage.py repair_searches --fix --search-id=<uuid> --muni=<subdomain>
"""

from django.core.management.base import BaseCommand, CommandError

from municipalities.models import Muni
from searches.models import Search


class Command(BaseCommand):
    help = "Identify and repair searches with missing municipality filters"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Fix a specific search (requires --search-id and --muni)",
        )
        parser.add_argument(
            "--search-id",
            type=str,
            help="UUID of the Search to fix",
        )
        parser.add_argument(
            "--muni",
            type=str,
            help="Subdomain of the municipality to add (e.g., 'alameda.ca')",
        )

    def handle(self, *args, **options):
        if options["fix"]:
            self._fix_search(options)
        else:
            self._list_broken_searches()

    def _list_broken_searches(self):
        """List all searches that have no municipalities configured."""
        self.stdout.write("Checking for searches with missing municipalities...\n")

        # Find searches with empty municipalities
        broken_searches = []
        for search in Search.objects.prefetch_related(
            "municipalities", "saved_by__user"
        ):
            if not search.municipalities.exists():
                broken_searches.append(search)

        if not broken_searches:
            self.stdout.write(
                self.style.SUCCESS("All searches have municipalities configured.")
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"Found {len(broken_searches)} search(es) with missing municipalities:\n"
            )
        )

        for search in broken_searches:
            self.stdout.write(f"\nSearch ID: {search.id}")
            self.stdout.write(f"  Search term: '{search.search_term}' or 'all updates'")
            self.stdout.write(f"  Created: {search.created}")
            self.stdout.write(f"  Document type: {search.document_type}")

            # Show saved searches using this Search
            saved_searches = search.saved_by.select_related("user").all()
            if saved_searches:
                self.stdout.write("  Used by SavedSearches:")
                for ss in saved_searches:
                    self.stdout.write(
                        f"    - '{ss.name}' (user: {ss.user.email}, id: {ss.id})"
                    )
            else:
                self.stdout.write("  Not used by any SavedSearch (orphaned)")

        self.stdout.write("\n" + "-" * 60)
        self.stdout.write("To fix a search, run:")
        self.stdout.write(
            "  python manage.py repair_searches --fix --search-id=<uuid> --muni=<subdomain>"
        )
        self.stdout.write("\nAvailable municipalities:")
        for muni in Muni.objects.all().order_by("subdomain")[:20]:
            self.stdout.write(f"  - {muni.subdomain} ({muni.name}, {muni.state})")
        if Muni.objects.count() > 20:
            self.stdout.write(f"  ... and {Muni.objects.count() - 20} more")

    def _fix_search(self, options):
        """Fix a specific search by adding a municipality."""
        search_id = options.get("search_id")
        muni_subdomain = options.get("muni")

        if not search_id:
            raise CommandError("--search-id is required when using --fix")
        if not muni_subdomain:
            raise CommandError("--muni is required when using --fix")

        # Find the search
        try:
            search = Search.objects.get(id=search_id)
        except Search.DoesNotExist:
            raise CommandError(f"Search with ID '{search_id}' not found") from None

        # Find the municipality
        try:
            muni = Muni.objects.get(subdomain=muni_subdomain)
        except Muni.DoesNotExist:
            raise CommandError(
                f"Municipality with subdomain '{muni_subdomain}' not found"
            ) from None

        # Check current state
        current_munis = list(search.municipalities.all())
        if muni in current_munis:
            self.stdout.write(
                self.style.WARNING(
                    f"Municipality '{muni.subdomain}' is already in this search"
                )
            )
            return

        # Add the municipality
        search.municipalities.add(muni)
        self.stdout.write(
            self.style.SUCCESS(
                f"Added municipality '{muni.name}' ({muni.subdomain}) to search {search.id}"
            )
        )

        # Show affected saved searches
        saved_searches = search.saved_by.select_related("user").all()
        if saved_searches:
            self.stdout.write("Affected SavedSearches:")
            for ss in saved_searches:
                self.stdout.write(f"  - '{ss.name}' (user: {ss.user.email})")
