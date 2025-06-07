"""
Tests for searches admin functionality.
"""

from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from municipalities.models import Muni
from searches.admin import SavedSearchAdmin, SearchAdmin
from searches.models import SavedSearch, Search

User = get_user_model()


@pytest.mark.django_db
class TestSearchAdmin:
    def test_populate_test_results_action(self):
        """Test the populate_test_results admin action"""
        # Create test data
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search1 = Search.objects.create(muni=muni, search_term="budget")
        search2 = Search.objects.create(muni=muni, all_results=True)

        # Create admin and mock request
        admin = SearchAdmin(Search, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create queryset
        queryset = Search.objects.filter(id__in=[search1.id, search2.id])

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action
            admin.populate_test_results(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Refresh from database
        search1.refresh_from_db()
        search2.refresh_from_db()

        # Verify results were populated
        assert (
            search1.agenda_match_json is not None
            or search1.minutes_match_json is not None
        )
        assert search1.last_fetched is not None

        assert (
            search2.agenda_match_json is not None
            or search2.minutes_match_json is not None
        )
        assert search2.last_fetched is not None

    def test_clear_search_results_action(self):
        """Test the clear_search_results admin action"""
        # Create test data with existing results
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni,
            search_term="budget",
            agenda_match_json=[{"meeting": "Council", "date": "2024-01-01"}],
            minutes_match_json=[{"meeting": "Council", "date": "2024-01-01"}],
        )

        # Create admin and mock request
        admin = SearchAdmin(Search, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create queryset
        queryset = Search.objects.filter(id=search.id)

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action
            admin.clear_search_results(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Refresh from database
        search.refresh_from_db()

        # Verify results were cleared
        assert search.agenda_match_json is None
        assert search.minutes_match_json is None
        assert search.last_agenda_matched is None
        assert search.last_minutes_matched is None


@pytest.mark.django_db
class TestSavedSearchAdmin:
    def test_create_test_saved_searches_action(self):
        """Test the create_test_saved_searches admin action"""
        # Create test data including user
        User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        Search.objects.create(muni=muni, search_term="budget")

        # Create admin and mock request
        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create empty queryset (action doesn't use it)
        queryset = SavedSearch.objects.none()

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action
            admin.create_test_saved_searches(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Verify saved searches were created
        assert SavedSearch.objects.count() > 0

    def test_create_test_saved_searches_no_users(self):
        """Test the action handles no users gracefully"""
        # Create admin and mock request
        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create empty queryset
        queryset = SavedSearch.objects.none()

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action (should handle no users gracefully)
            admin.create_test_saved_searches(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Verify no saved searches were created
        assert SavedSearch.objects.count() == 0

    def test_create_test_saved_searches_no_searches(self):
        """Test the action handles no searches gracefully"""

        # Create admin and mock request
        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create empty queryset
        queryset = SavedSearch.objects.none()

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action (should handle no searches gracefully)
            admin.create_test_saved_searches(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Verify no saved searches were created
        assert SavedSearch.objects.count() == 0

    def test_populate_search_results_for_selected_action(self):
        """Test the populate_search_results_for_selected admin action"""
        # Create test data
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )

        # Create admin and mock request
        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create queryset
        queryset = SavedSearch.objects.filter(id=saved_search.id)

        # Mock the message_user method to avoid message framework issues
        with patch.object(admin, "message_user") as mock_message:
            # Call the action
            admin.populate_search_results_for_selected(request, queryset)

            # Verify message_user was called
            mock_message.assert_called_once()

        # Refresh from database
        search.refresh_from_db()

        # Verify results were populated
        assert (
            search.agenda_match_json is not None
            or search.minutes_match_json is not None
        )
        assert search.last_fetched is not None

    def test_populate_search_results_avoids_duplicates(self):
        """Test that the action only updates each search once"""
        # Create test data with multiple saved searches for same search
        user1 = User.objects.create_user(  # type: ignore
            username="user1", email="user1@example.com", password="testpass"
        )
        user2 = User.objects.create_user(  # type: ignore
            username="user2", email="user2@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search1 = SavedSearch.objects.create(
            user=user1, search=search, name="User1 Budget"
        )
        saved_search2 = SavedSearch.objects.create(
            user=user2, search=search, name="User2 Budget"
        )

        # Create admin and mock request
        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        factory = RequestFactory()
        request = factory.get("/")
        request.user = Mock()

        # Create queryset with both saved searches
        queryset = SavedSearch.objects.filter(
            id__in=[saved_search1.id, saved_search2.id]
        )

        # Spy on the populate method to verify it's called only once
        with patch(
            "searches.test_data_utils.TestDataGenerator.populate_search_with_test_data"
        ) as mock_populate:
            with patch.object(admin, "message_user") as mock_message:
                admin.populate_search_results_for_selected(request, queryset)

                # Should be called only once even though there are 2 saved searches
                mock_populate.assert_called_once_with(search)
                # Verify message_user was called
                mock_message.assert_called_once()
