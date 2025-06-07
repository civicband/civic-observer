from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from municipalities.models import Muni
from searches.models import SavedSearch, Search

User = get_user_model()


class Command(BaseCommand):
    help = "Display summary of test data in the system"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=== Test Data Summary ===\n"))

        # Municipalities
        munis = Muni.objects.all()
        self.stdout.write(f"📍 Municipalities: {munis.count()}")
        for muni in munis[:5]:  # Show first 5
            self.stdout.write(f"   • {muni.name}, {muni.state} ({muni.subdomain})")
        if munis.count() > 5:
            self.stdout.write(f"   ... and {munis.count() - 5} more")

        # Users
        users = User.objects.all()
        self.stdout.write(f"\n👥 Users: {users.count()}")
        for user in users[:5]:  # Show first 5
            self.stdout.write(f"   • {user.email}")  # type: ignore
        if users.count() > 5:
            self.stdout.write(f"   ... and {users.count() - 5} more")

        # Searches
        searches = Search.objects.all()
        searches_with_results = searches.exclude(
            agenda_match_json__isnull=True, minutes_match_json__isnull=True
        )
        self.stdout.write(f"\n🔍 Searches: {searches.count()}")
        self.stdout.write(f"   • With results: {searches_with_results.count()}")
        self.stdout.write(
            f"   • Without results: {searches.count() - searches_with_results.count()}"
        )

        # Show sample searches
        if searches_with_results.exists():
            self.stdout.write("\n   Sample searches with results:")
            for search in searches_with_results[:3]:
                agenda_count = (
                    len(search.agenda_match_json) if search.agenda_match_json else 0
                )
                minutes_count = (
                    len(search.minutes_match_json) if search.minutes_match_json else 0
                )
                self.stdout.write(
                    f"   • {search} -> {agenda_count} agenda, {minutes_count} minutes"
                )

        # Saved Searches
        saved_searches = SavedSearch.objects.all()
        self.stdout.write(f"\n💾 Saved Searches: {saved_searches.count()}")

        if saved_searches.exists():
            self.stdout.write("   Sample saved searches:")
            for saved_search in saved_searches[:3]:
                has_results = bool(
                    saved_search.search.agenda_match_json
                    or saved_search.search.minutes_match_json
                )
                status = "✓" if has_results else "✗"
                self.stdout.write(f"   • {saved_search.name} [{status}]")

        # Quick stats
        total_agenda_results = sum(
            len(s.agenda_match_json) if s.agenda_match_json else 0
            for s in searches_with_results
        )
        total_minutes_results = sum(
            len(s.minutes_match_json) if s.minutes_match_json else 0
            for s in searches_with_results
        )

        self.stdout.write("\n📊 Total simulated results:")
        self.stdout.write(f"   • Agenda matches: {total_agenda_results}")
        self.stdout.write(f"   • Minutes matches: {total_minutes_results}")
        self.stdout.write(
            f"   • Combined total: {total_agenda_results + total_minutes_results}"
        )

        if not searches_with_results.exists():
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  No searches with results found. Run 'populate_test_searches' to create test data."
                )
            )

        if not saved_searches.exists():
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  No saved searches found. Run 'populate_test_savedsearches' to create test data."
                )
            )

        self.stdout.write(self.style.SUCCESS("\n=== End Summary ==="))
