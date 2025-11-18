"""
Tests for searches management commands.
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from municipalities.models import Muni
from searches.models import SavedSearch, Search

User = get_user_model()


@pytest.mark.django_db
class TestPopulateTestSearches:
    def test_populate_test_searches_command(self):
        """Test the populate_test_searches management command"""
        out = StringIO()

        # Run the command with higher count to reduce flakiness from random duplicates
        call_command("populate_test_searches", "--count=10", stdout=out)

        # Verify searches were created (may be less due to duplicates)
        assert Search.objects.count() >= 5

        # Verify output contains expected information
        output = out.getvalue()
        assert "Successfully created" in output
        assert "test searches with simulated results" in output

        # Verify some searches have results
        searches_with_results = Search.objects.exclude(
            agenda_match_json__isnull=True, minutes_match_json__isnull=True
        )
        assert searches_with_results.count() > 0

    def test_populate_test_searches_with_clear(self):
        """Test the command with --clear flag"""
        # Create existing search
        muni = Muni.objects.create(
            subdomain="existing", name="Existing City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="existing")

        assert Search.objects.count() == 1

        out = StringIO()

        # Run command with --clear
        call_command("populate_test_searches", "--count=2", "--clear", stdout=out)

        # Verify old searches were cleared and new ones created
        assert Search.objects.count() >= 1
        assert not Search.objects.filter(search_term="existing").exists()

        # Verify output mentions clearing
        output = out.getvalue()
        assert "Clearing existing search data" in output
        assert "Cleared existing search data" in output

    def test_populate_test_searches_creates_municipalities(self):
        """Test that the command creates test municipalities"""
        assert Muni.objects.count() == 0

        out = StringIO()
        call_command("populate_test_searches", "--count=1", stdout=out)

        # Should have created test municipalities
        assert Muni.objects.count() > 0

        # Check for expected test municipalities
        assert Muni.objects.filter(subdomain="sf").exists()
        assert Muni.objects.filter(subdomain="oakland").exists()

    def test_populate_test_searches_handles_existing_municipalities(self):
        """Test command works with existing municipalities"""
        # Create existing municipality (matching management command format)
        Muni.objects.create(
            subdomain="sf", name="San Francisco", state="CA", kind="City"
        )

        out = StringIO()
        # Use higher count to ensure we get searches for existing muni
        call_command("populate_test_searches", "--count=10", stdout=out)

        # Should not create duplicate municipalities
        assert Muni.objects.filter(subdomain="sf").count() == 1

        # Should create some searches (not necessarily with the specific muni due to randomness)
        assert Search.objects.count() > 0

        # All created municipalities should be in the expected test set
        expected_subdomains = {"sf", "oakland", "berkeley", "palo-alto"}
        actual_subdomains = set(Muni.objects.values_list("subdomain", flat=True))
        assert actual_subdomains.issubset(expected_subdomains)


@pytest.mark.django_db
class TestPopulateTestSavedSearches:
    def test_populate_test_savedsearches_command(self):
        """Test the populate_test_savedsearches management command"""
        # Create prerequisite data
        User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        out = StringIO()

        # Run the command
        call_command("populate_test_savedsearches", "--count=5", stdout=out)

        # Verify some saved searches were created (may be less than requested due to duplicates)
        assert SavedSearch.objects.count() >= 1

        # Verify output contains expected information
        output = out.getvalue()
        assert "Successfully created" in output
        assert "test saved searches" in output

    def test_populate_test_savedsearches_with_clear(self):
        """Test the command with --clear flag"""
        # Create prerequisite data
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")

        # Create existing saved search
        SavedSearch.objects.create(user=user, search=search, name="Existing")
        assert SavedSearch.objects.count() == 1

        out = StringIO()

        # Run command with --clear
        call_command("populate_test_savedsearches", "--count=2", "--clear", stdout=out)

        # Verify old saved searches were cleared and new ones created
        assert SavedSearch.objects.count() >= 1
        assert not SavedSearch.objects.filter(name="Existing").exists()

        # Verify output mentions clearing
        output = out.getvalue()
        assert "Clearing existing saved search data" in output
        assert "Cleared existing saved search data" in output

    def test_populate_test_savedsearches_no_searches(self):
        """Test command handles no searches gracefully"""
        out = StringIO()

        # Run the command
        call_command("populate_test_savedsearches", "--count=5", stdout=out)

        # Should not create any saved searches
        assert SavedSearch.objects.count() == 0

        # Should show error message
        output = out.getvalue()
        assert "No Search objects found" in output

    def test_populate_test_savedsearches_no_users(self):
        """Test command handles no users gracefully"""
        # Create search but no users
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        out = StringIO()

        # Run the command
        call_command("populate_test_savedsearches", "--count=5", stdout=out)

        # Should not create any saved searches
        assert SavedSearch.objects.count() == 0

        # Should show error message
        output = out.getvalue()
        assert "No users found" in output

    def test_populate_test_savedsearches_create_users(self):
        """Test command with --create-users flag"""
        # Create search but no users
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        assert User.objects.count() == 0

        out = StringIO()

        # Run command with --create-users
        call_command(
            "populate_test_savedsearches", "--count=2", "--create-users", stdout=out
        )

        # Should have created users and saved searches
        assert User.objects.count() > 0
        assert (
            SavedSearch.objects.count() >= 1
        )  # May be less than count due to duplicates

        # Should show user creation in output
        output = out.getvalue()
        # Note: exact output depends on whether users already exist
        assert "test saved searches" in output

    def test_populate_test_savedsearches_uses_existing_users(self):
        """Test command uses existing users when available"""
        # Create existing user and search
        existing_user = User.objects.create_user(  # type: ignore
            username="existing", email="existing@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        out = StringIO()

        # Run command (should use existing user)
        call_command("populate_test_savedsearches", "--count=1", stdout=out)

        # Should have created saved search with existing user
        assert SavedSearch.objects.count() == 1
        assert SavedSearch.objects.first().user == existing_user  # type: ignore

        # Should mention using existing users
        output = out.getvalue()
        assert "Using 1 existing users" in output


@pytest.mark.django_db
class TestShowTestData:
    def test_show_test_data_empty_database(self):
        """Test show_test_data command with empty database"""
        out = StringIO()

        call_command("show_test_data", stdout=out)

        output = out.getvalue()
        assert "Test Data Summary" in output
        assert "Municipalities: 0" in output
        assert "Users: 0" in output
        assert "Searches: 0" in output
        assert "Saved Searches: 0" in output
        assert "No searches with results found" in output
        assert "No saved searches found" in output

    def test_show_test_data_with_data(self):
        """Test show_test_data command with test data"""
        # Create test data
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni,
            search_term="budget",
            agenda_match_json=[
                {
                    "meeting": "Council",
                    "date": "2024-01-01",
                    "text": "Budget discussion",
                }
            ],
            minutes_match_json=[
                {"meeting": "Council", "date": "2024-01-01", "text": "Budget approved"}
            ],
        )
        SavedSearch.objects.create(user=user, search=search, name="Budget Updates")

        out = StringIO()

        call_command("show_test_data", stdout=out)

        output = out.getvalue()
        assert "Test Data Summary" in output
        assert "Municipalities: 1" in output
        assert "Test City" in output
        assert "Users: 1" in output
        assert "test@example.com" in output
        assert "Searches: 1" in output
        assert "With results: 1" in output
        assert "Without results: 0" in output
        assert "Saved Searches: 1" in output
        assert "Budget Updates [✓]" in output
        assert "Agenda matches: 1" in output
        assert "Minutes matches: 1" in output
        assert "Combined total: 2" in output

    def test_show_test_data_truncates_long_lists(self):
        """Test that command truncates long lists appropriately"""
        # Create many municipalities
        for i in range(10):
            Muni.objects.create(
                subdomain=f"city{i}", name=f"City {i}", state="CA", kind="city"
            )

        out = StringIO()

        call_command("show_test_data", stdout=out)

        output = out.getvalue()
        assert "Municipalities: 10" in output
        assert "... and 5 more" in output  # Should show truncation message

    def test_show_test_data_shows_searches_without_results(self):
        """Test command properly categorizes searches with and without results"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        # Create search with results
        Search.objects.create(
            muni=muni,
            search_term="budget",
            agenda_match_json=[{"meeting": "Council", "date": "2024-01-01"}],
        )

        # Create search without results
        Search.objects.create(muni=muni, search_term="empty")

        out = StringIO()

        call_command("show_test_data", stdout=out)

        output = out.getvalue()
        assert "Searches: 2" in output
        assert "With results: 1" in output
        assert "Without results: 1" in output
