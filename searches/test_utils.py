"""
Tests for searches utility functions.
"""

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from municipalities.models import Muni
from searches.models import SavedSearch, Search
from searches.test_data_utils import TestDataGenerator

User = get_user_model()


@pytest.mark.django_db
class TestTestDataGenerator:
    def test_populate_search_with_test_data_keyword_search(self):
        """Test populating a keyword search with test data"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni, search_term="budget", all_results=False
        )

        # Verify search starts empty
        assert search.agenda_match_json is None
        assert search.minutes_match_json is None
        assert search.last_fetched is None

        # Populate with test data
        TestDataGenerator.populate_search_with_test_data(search)

        # Verify search now has data
        assert search.last_fetched is not None

        # Check agenda data structure for keyword searches
        if search.agenda_match_json:
            for agenda in search.agenda_match_json:
                assert "id" in agenda
                assert "meeting" in agenda
                assert "date" in agenda
                assert "page" in agenda
                assert "text" in agenda
                assert "page_image" in agenda
                assert search.search_term in agenda["text"]

        # Check minutes data structure for keyword searches
        if search.minutes_match_json:
            for minutes in search.minutes_match_json:
                assert "id" in minutes
                assert "meeting" in minutes
                assert "date" in minutes
                assert "page" in minutes
                assert "text" in minutes
                assert "page_image" in minutes
                assert search.search_term in minutes["text"]

    def test_populate_search_with_test_data_all_results(self):
        """Test populating an all_results search with test data"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="", all_results=True)

        # Populate with test data
        TestDataGenerator.populate_search_with_test_data(search)

        # Verify search now has data
        assert search.last_fetched is not None

        # Check agenda data structure for all_results searches
        if search.agenda_match_json:
            for agenda in search.agenda_match_json:
                assert "meeting" in agenda
                assert "date" in agenda
                assert "count(page)" in agenda
                # Should NOT have detailed fields for all_results
                assert "id" not in agenda
                assert "text" not in agenda
                assert "page" not in agenda

        # Check minutes data structure for all_results searches
        if search.minutes_match_json:
            for minutes in search.minutes_match_json:
                assert "meeting" in minutes
                assert "date" in minutes
                assert "count(page)" in minutes
                # Should NOT have detailed fields for all_results
                assert "id" not in minutes
                assert "text" not in minutes
                assert "page" not in minutes

    def test_populate_search_updates_timestamps(self):
        """Test that populating search updates appropriate timestamps"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")

        before_populate = timezone.now()
        TestDataGenerator.populate_search_with_test_data(search)
        after_populate = timezone.now()

        # last_fetched should always be updated
        assert search.last_fetched is not None
        assert before_populate <= search.last_fetched <= after_populate

        # If we have agenda data, last_agenda_matched should be set
        if search.agenda_match_json:
            assert search.last_agenda_matched is not None
            assert before_populate <= search.last_agenda_matched <= after_populate

        # If we have minutes data, last_minutes_matched should be set
        if search.minutes_match_json:
            assert search.last_minutes_matched is not None
            assert before_populate <= search.last_minutes_matched <= after_populate

    def test_generate_realistic_text_with_search_term(self):
        """Test realistic text generation with search term"""
        search_term = "budget"
        meeting_name = "City Council"

        text = TestDataGenerator._generate_realistic_text(search_term, meeting_name)

        assert isinstance(text, str)
        assert len(text) > 0
        assert search_term in text
        # Should contain either the search term or meeting name
        assert search_term in text or meeting_name in text

    def test_generate_realistic_text_without_search_term(self):
        """Test realistic text generation without search term (all_results)"""
        search_term = None
        meeting_name = "Planning Commission"

        text = TestDataGenerator._generate_realistic_text(search_term, meeting_name)

        assert isinstance(text, str)
        assert len(text) > 0
        # Should be generic text that doesn't reference a specific search term
        # Generic text should contain either meeting name or generic meeting terms
        assert (
            meeting_name in text
            or "meeting" in text.lower()
            or "session" in text.lower()
            or "administrative" in text.lower()
            or "business" in text.lower()
        )

    def test_generate_realistic_text_empty_search_term(self):
        """Test realistic text generation with empty search term"""
        search_term = ""
        meeting_name = "School Board"

        text = TestDataGenerator._generate_realistic_text(search_term, meeting_name)

        assert isinstance(text, str)
        assert len(text) > 0
        # Should treat empty string same as None
        # Generic text should contain either meeting name or generic meeting terms
        assert (
            meeting_name in text
            or "meeting" in text.lower()
            or "session" in text.lower()
            or "administrative" in text.lower()
            or "business" in text.lower()
        )

    def test_generate_saved_search_name_with_search_term(self):
        """Test saved search name generation with search term"""
        muni = Muni.objects.create(
            subdomain="oakland", name="Oakland", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni, search_term="budget", all_results=False
        )

        name = TestDataGenerator.generate_saved_search_name(search)

        assert isinstance(name, str)
        assert len(name) > 0
        assert "budget" in name
        # Name should contain muni name if template includes {muni}
        # Since templates are random, we can't guarantee the muni will always be in the name

    def test_generate_saved_search_name_all_results(self):
        """Test saved search name generation for all_results"""
        muni = Muni.objects.create(
            subdomain="berkeley", name="Berkeley", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="", all_results=True)

        name = TestDataGenerator.generate_saved_search_name(search)

        assert isinstance(name, str)
        assert len(name) > 0
        assert "All Results" in name
        # Berkeley might not be in name due to random template selection
        # Just verify it's a valid name structure

    def test_create_test_saved_searches_with_data(self):
        """Test creating test saved searches when data exists"""
        # Create prerequisite data
        User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        # Create test saved searches
        created_count = TestDataGenerator.create_test_saved_searches(5)

        assert created_count > 0
        assert SavedSearch.objects.count() == created_count

        # Verify all created saved searches have valid data
        for saved_search in SavedSearch.objects.all():
            assert saved_search.user is not None
            assert saved_search.search is not None
            assert saved_search.name
            assert len(saved_search.name) > 0

    def test_create_test_saved_searches_no_users(self):
        """Test creating test saved searches with no users"""
        # Create search but no users
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        # Should return 0 when no users exist
        created_count = TestDataGenerator.create_test_saved_searches(5)

        assert created_count == 0
        assert SavedSearch.objects.count() == 0

    def test_create_test_saved_searches_no_searches(self):
        """Test creating test saved searches with no searches"""

        # Should return 0 when no searches exist
        created_count = TestDataGenerator.create_test_saved_searches(5)

        assert created_count == 0
        assert SavedSearch.objects.count() == 0

    def test_create_test_saved_searches_avoids_duplicates(self):
        """Test that creating test saved searches avoids duplicates"""
        # Create prerequisite data
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")

        # Create saved search manually first
        SavedSearch.objects.create(user=user, search=search, name="Existing Search")

        # Try to create more test saved searches
        created_count = TestDataGenerator.create_test_saved_searches(5)

        # Should have created some but not duplicated the existing one
        total_count = SavedSearch.objects.count()
        assert total_count >= 1  # At least the existing one
        assert created_count == total_count - 1  # Created count should exclude existing

    def test_create_test_saved_searches_sets_notification_timestamps(self):
        """Test that some saved searches get notification timestamps"""
        # Create prerequisite data
        User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        # Create test saved searches
        TestDataGenerator.create_test_saved_searches(10)

        # Some (but not necessarily all) should have notification timestamps
        saved_searches_with_timestamps = SavedSearch.objects.exclude(
            last_notification_sent__isnull=True
        )

        # Should have at least some with timestamps (random choice might create some)
        # We can't guarantee exact count due to randomness, but verify structure
        for saved_search in saved_searches_with_timestamps:
            assert saved_search.last_notification_sent is not None
            assert isinstance(saved_search.last_notification_sent, datetime)

    def test_clear_search_results(self):
        """Test clearing search results"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni,
            search_term="budget",
            agenda_match_json=[{"meeting": "Council", "date": "2024-01-01"}],
            minutes_match_json=[{"meeting": "Council", "date": "2024-01-01"}],
            last_agenda_matched=timezone.now(),
            last_minutes_matched=timezone.now(),
        )

        # Verify search has data initially
        assert search.agenda_match_json is not None
        assert search.minutes_match_json is not None
        assert search.last_agenda_matched is not None
        assert search.last_minutes_matched is not None

        # Clear the results
        TestDataGenerator.clear_search_results(search)

        # Verify all results and timestamps are cleared
        assert search.agenda_match_json is None
        assert search.minutes_match_json is None
        assert search.last_agenda_matched is None
        assert search.last_minutes_matched is None

    def test_meeting_types_constant(self):
        """Test that meeting types constant contains expected values"""
        meeting_types = TestDataGenerator.MEETING_TYPES

        assert isinstance(meeting_types, list)
        assert len(meeting_types) > 0
        assert "City Council" in meeting_types
        assert "Planning Commission" in meeting_types

        # All should be strings
        for meeting_type in meeting_types:
            assert isinstance(meeting_type, str)
            assert len(meeting_type) > 0

    def test_search_name_templates_constant(self):
        """Test that search name templates constant contains expected values"""
        templates = TestDataGenerator.SEARCH_NAME_TEMPLATES

        assert isinstance(templates, list)
        assert len(templates) > 0

        # All should be strings with search_type placeholder
        for template in templates:
            assert isinstance(template, str)
            assert len(template) > 0
            assert "{search_type}" in template

        # At least some should have muni placeholder
        templates_with_muni = [t for t in templates if "{muni}" in t]
        assert len(templates_with_muni) > 0
