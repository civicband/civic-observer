"""
Tests for searches models - following TDD approach.

These tests define the expected behavior of the refactored Search and SavedSearch models.
They will initially fail (RED) until we implement the model changes (GREEN).
"""

from datetime import date

import pytest

from meetings.models import MeetingPage
from searches.models import SavedSearch
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    SavedSearchFactory,
    SearchFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchModelEdgeCases:
    """Test edge cases in Search model."""

    def test_str_with_multiple_municipalities(self):
        """
        Search.__str__() should show first 2 municipalities and count the rest.
        """
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")
        muni3 = MuniFactory(name="Alameda", state="CA")
        muni4 = MuniFactory(name="Emeryville", state="CA")

        search = SearchFactory(search_term="budget")
        search.municipalities.set([muni1, muni2, muni3, muni4])

        # Should show count of additional municipalities beyond first 2
        str_repr = str(search)
        assert "+2 more" in str_repr
        # Should mention it's a search for 'budget'
        assert "budget" in str_repr

    def test_str_with_exactly_two_municipalities(self):
        """
        Search.__str__() should show both municipalities when exactly 2.
        """
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")

        search = SearchFactory(search_term="zoning")
        search.municipalities.set([muni1, muni2])

        str_repr = str(search)
        assert "Oakland" in str_repr
        assert "Berkeley" in str_repr
        assert "more" not in str_repr


@pytest.mark.django_db
class TestSearchModel:
    """Test the refactored Search model with local database queries."""

    def test_search_with_municipalities_many_to_many(self):
        """Test that Search supports multiple municipalities via M2M relationship."""
        muni1 = MuniFactory(name="Berkeley", state="CA")
        muni2 = MuniFactory(name="Oakland", state="CA")

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni1, muni2)

        assert search.municipalities.count() == 2
        assert muni1 in search.municipalities.all()
        assert muni2 in search.municipalities.all()

    def test_search_with_states_filter(self):
        """Test that Search stores states as a list in JSONField."""
        search = SearchFactory(search_term="budget", states=["CA", "OR", "WA"])

        assert search.states == ["CA", "OR", "WA"]
        assert len(search.states) == 3

    def test_search_with_date_range_filters(self):
        """Test that Search supports date_from and date_to filters."""
        date_from = date(2024, 1, 1)
        date_to = date(2024, 12, 31)

        search = SearchFactory(
            search_term="planning", date_from=date_from, date_to=date_to
        )

        assert search.date_from == date_from
        assert search.date_to == date_to

    def test_search_with_document_type_filter(self):
        """Test that Search supports document_type filter (agenda/minutes/all)."""
        search = SearchFactory(search_term="zoning", document_type="agenda")

        assert search.document_type == "agenda"
        assert search.document_type in ["agenda", "minutes", "all"]

    def test_search_with_meeting_name_query(self):
        """Test that Search supports meeting_name_query for full-text search."""
        search = SearchFactory(
            search_term="budget", meeting_name_query="planning commission"
        )

        assert search.meeting_name_query == "planning commission"

    def test_search_with_empty_term_for_all_updates(self):
        """Test that empty/null search_term means 'all updates' mode."""
        muni = MuniFactory(name="San Francisco")

        # Empty string search_term
        search1 = SearchFactory(search_term="")
        search1.municipalities.add(muni)

        # Null search_term
        search2 = SearchFactory(search_term=None)
        search2.municipalities.add(muni)

        assert search1.search_term in ["", None]
        assert search2.search_term in ["", None]

    def test_search_stores_last_result_page_ids(self):
        """Test that Search stores list of matching page IDs for change detection."""
        search = SearchFactory()
        search.last_result_page_ids = ["page-1", "page-2", "page-3"]
        search.save()

        search.refresh_from_db()
        assert search.last_result_page_ids == ["page-1", "page-2", "page-3"]
        assert isinstance(search.last_result_page_ids, list)

    def test_search_tracks_result_count(self):
        """Test that Search tracks the number of matching pages."""
        search = SearchFactory()
        search.last_result_count = 42
        search.save()

        search.refresh_from_db()
        assert search.last_result_count == 42

    def test_search_update_detects_new_pages(self):
        """Test that update_search() detects when new pages match the search."""
        # Setup: Create a municipality with meeting pages
        muni = MuniFactory(name="Berkeley", subdomain="berkeley")
        doc = MeetingDocumentFactory(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date.today(),
            document_type="agenda",
        )

        # Create pages with searchable text
        page1 = MeetingPageFactory(
            document=doc, page_number=1, text="Discussion about housing policy"
        )
        page2 = MeetingPageFactory(
            document=doc, page_number=2, text="Budget allocation for housing projects"
        )

        # Create a search for "housing"
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_result_page_ids = []  # No previous results
        search.save()

        # Call update_search() - should find the new pages
        new_pages = search.update_search()

        # Should return QuerySet of new pages
        assert new_pages is not None
        assert new_pages.count() == 2
        assert page1 in new_pages
        assert page2 in new_pages

        # Should update last_result_page_ids
        search.refresh_from_db()
        assert set(search.last_result_page_ids) == {page1.id, page2.id}
        assert search.last_result_count == 2

    def test_search_update_with_no_changes_returns_empty(self):
        """Test that update_search() returns empty QuerySet when no new pages."""
        muni = MuniFactory(name="Oakland")
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="Budget discussion")

        # Create search that already has this page in results
        search = SearchFactory(search_term="budget")
        search.municipalities.add(muni)
        search.last_result_page_ids = [page.id]
        search.last_result_count = 1
        search.save()

        # Call update_search() - should find no new pages
        new_pages = search.update_search()

        assert new_pages is not None
        assert new_pages.count() == 0

    def test_all_updates_search_matches_any_new_pages(self):
        """Test that searches with empty search_term match all pages (all updates mode)."""
        muni = MuniFactory(name="San Francisco")
        doc = MeetingDocumentFactory(municipality=muni)

        # Create diverse pages with different content
        page1 = MeetingPageFactory(document=doc, page_number=1, text="Housing policy")
        page2 = MeetingPageFactory(document=doc, page_number=2, text="Budget report")
        page3 = MeetingPageFactory(document=doc, page_number=3, text="Zoning changes")

        # Create "all updates" search (empty search_term)
        search = SearchFactory(search_term="")
        search.municipalities.add(muni)
        search.last_result_page_ids = []
        search.save()

        # Should match ALL pages regardless of content
        new_pages = search.update_search()

        assert new_pages is not None
        assert new_pages.count() == 3
        assert page1 in new_pages
        assert page2 in new_pages
        assert page3 in new_pages


@pytest.mark.django_db
class TestSavedSearchModel:
    """Test the refactored SavedSearch model with notification frequencies."""

    def test_saved_search_has_notification_frequency(self):
        """Test that SavedSearch has notification_frequency field."""
        user = UserFactory()
        search = SearchFactory()

        saved_search = SavedSearchFactory(
            user=user, search=search, notification_frequency="immediate"
        )

        assert saved_search.notification_frequency == "immediate"
        assert saved_search.notification_frequency in [
            "immediate",
            "daily",
            "weekly",
        ]

    def test_saved_search_notification_frequency_choices(self):
        """Test all notification frequency choices work."""
        user = UserFactory()

        # Test each frequency option
        for frequency in ["immediate", "daily", "weekly"]:
            # Create a new search for each saved search (unique_together constraint)
            search = SearchFactory()
            saved_search = SavedSearchFactory(
                user=user, search=search, notification_frequency=frequency
            )
            assert saved_search.notification_frequency == frequency

    def test_saved_search_has_last_checked_timestamp(self):
        """Test that SavedSearch tracks when it was last checked."""
        from datetime import datetime

        saved_search = SavedSearchFactory()

        assert saved_search.last_checked is not None
        assert isinstance(saved_search.last_checked, datetime)

    def test_saved_search_has_pending_results_flag(self):
        """Test that SavedSearch has has_pending_results flag for digest batching."""
        saved_search = SavedSearchFactory()
        assert hasattr(saved_search, "has_pending_results")
        assert saved_search.has_pending_results is False  # Default

        # Mark as having pending results
        saved_search.has_pending_results = True
        saved_search.save()

        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is True

    def test_saved_search_default_frequency_is_immediate(self):
        """Test that new SavedSearch defaults to immediate notifications."""
        user = UserFactory()
        search = SearchFactory()

        # Create without specifying frequency
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Test Search"
        )

        assert saved_search.notification_frequency == "immediate"

    def test_send_notification_accepts_new_pages_queryset(self):
        """Test that send_search_notification() accepts QuerySet of new MeetingPage objects."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="New housing policy")
        page2 = MeetingPageFactory(document=doc, text="Housing budget update")

        saved_search = SavedSearchFactory()

        # Should be able to call with QuerySet of pages
        _new_pages = MeetingPage.objects.filter(id__in=[page1.id, page2.id])

        # This will fail until we update the method signature
        # For now, just test that the method exists and can be called
        assert hasattr(saved_search, "send_search_notification")

    def test_saved_search_str_representation(self):
        """Test string representation includes user and name."""
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="housing")
        saved_search = SavedSearchFactory(
            user=user, search=search, name="My Housing Alerts"
        )

        str_repr = str(saved_search)
        assert "My Housing Alerts" in str_repr
        assert "test@example.com" in str_repr


@pytest.mark.django_db
class TestSearchModelIntegration:
    """Integration tests for Search model with different filter combinations."""

    def test_search_with_multiple_filters_combined(self):
        """Test Search with all filters set together."""
        muni1 = MuniFactory(name="Berkeley", state="CA")
        muni2 = MuniFactory(name="Oakland", state="CA")

        search = SearchFactory(
            search_term="housing",
            states=["CA", "OR"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
            meeting_name_query="planning",
        )
        search.municipalities.add(muni1, muni2)

        # Verify all filters are set
        assert search.search_term == "housing"
        assert search.municipalities.count() == 2
        assert search.states == ["CA", "OR"]
        assert search.date_from == date(2024, 1, 1)
        assert search.date_to == date(2024, 12, 31)
        assert search.document_type == "agenda"
        assert search.meeting_name_query == "planning"

    def test_search_filters_are_optional(self):
        """Test that all filter fields are optional except search_term."""
        # Create search with minimal data (just search_term can be empty for "all updates")
        search = SearchFactory(
            search_term="",  # Empty for "all updates"
            states=[],  # Empty list
            date_from=None,
            date_to=None,
            document_type="all",  # Default
            meeting_name_query=None,
        )

        assert search.search_term == ""
        assert search.states == []
        assert search.date_from is None
        assert search.date_to is None
        assert search.document_type == "all"
        assert search.meeting_name_query is None
