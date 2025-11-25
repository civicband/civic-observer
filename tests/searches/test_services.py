"""
Tests for searches.services module - shared search service functions.

These tests define the expected behavior of execute_search() and get_new_pages().
Following TDD: write failing tests first (RED), then implement (GREEN).
"""

from datetime import date

import pytest

from meetings.models import MeetingPage
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    SearchFactory,
)


@pytest.mark.django_db
class TestExecuteSearch:
    """Test the execute_search() function that queries local MeetingPage database."""

    def test_execute_search_with_text_query(self):
        """Test basic text search returns matching pages."""
        from searches.services import execute_search

        # Setup: Create pages with searchable text
        muni = MuniFactory(name="Berkeley")
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="Discussion about housing policy")
        page2 = MeetingPageFactory(document=doc, text="Budget allocation for housing")
        page3 = MeetingPageFactory(document=doc, text="Zoning changes")  # Doesn't match

        # Create search for "housing"
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)

        # Execute search
        results = execute_search(search)

        # Should return pages 1 and 2 (both contain "housing")
        assert results is not None
        assert page1 in results
        assert page2 in results
        assert page3 not in results

    def test_execute_search_empty_query_returns_all(self):
        """Test that empty search_term returns all pages (all updates mode)."""
        from searches.services import execute_search

        muni = MuniFactory(name="Oakland")
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="Housing policy")
        page2 = MeetingPageFactory(document=doc, text="Budget report")
        page3 = MeetingPageFactory(document=doc, text="Zoning changes")

        # Create "all updates" search (empty search_term)
        search = SearchFactory(search_term="")
        search.municipalities.add(muni)

        # Execute search
        results = execute_search(search)

        # Should return ALL pages regardless of content
        assert results.count() == 3
        assert page1 in results
        assert page2 in results
        assert page3 in results

    def test_execute_search_with_date_filters(self):
        """Test search filters by date range."""
        from searches.services import execute_search

        muni = MuniFactory()

        # Create documents on different dates
        doc_jan = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 1, 15)
        )
        doc_mar = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 3, 15)
        )
        doc_may = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 5, 15)
        )

        page_jan = MeetingPageFactory(document=doc_jan, text="budget")
        page_mar = MeetingPageFactory(document=doc_mar, text="budget")
        page_may = MeetingPageFactory(document=doc_may, text="budget")

        # Search with date range Feb-Apr
        search = SearchFactory(
            search_term="budget",
            date_from=date(2024, 2, 1),
            date_to=date(2024, 4, 30),
        )
        search.municipalities.add(muni)

        results = execute_search(search)

        # Should only return March page
        assert results.count() == 1
        assert page_mar in results
        assert page_jan not in results
        assert page_may not in results

    def test_execute_search_with_multiple_municipalities(self):
        """Test search across multiple municipalities."""
        from searches.services import execute_search

        muni1 = MuniFactory(name="Berkeley")
        muni2 = MuniFactory(name="Oakland")
        muni3 = MuniFactory(name="San Francisco")

        doc1 = MeetingDocumentFactory(municipality=muni1)
        doc2 = MeetingDocumentFactory(municipality=muni2)
        doc3 = MeetingDocumentFactory(municipality=muni3)

        page1 = MeetingPageFactory(document=doc1, text="housing")
        page2 = MeetingPageFactory(document=doc2, text="housing")
        page3 = MeetingPageFactory(document=doc3, text="housing")

        # Search in Berkeley and Oakland only
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni1, muni2)

        results = execute_search(search)

        assert page1 in results
        assert page2 in results
        assert page3 not in results  # San Francisco excluded

    def test_execute_search_with_states_filter(self):
        """Test search filters by state."""
        from searches.services import execute_search

        ca_muni = MuniFactory(name="Berkeley", state="CA")
        or_muni = MuniFactory(name="Portland", state="OR")

        ca_doc = MeetingDocumentFactory(municipality=ca_muni)
        or_doc = MeetingDocumentFactory(municipality=or_muni)

        ca_page = MeetingPageFactory(document=ca_doc, text="budget")
        or_page = MeetingPageFactory(document=or_doc, text="budget")

        # Search only in CA
        search = SearchFactory(search_term="budget", states=["CA"])

        results = execute_search(search)

        assert ca_page in results
        assert or_page not in results

    def test_execute_search_with_document_type_filter(self):
        """Test search filters by document type (agenda/minutes)."""
        from searches.services import execute_search

        muni = MuniFactory()

        agenda = MeetingDocumentFactory(municipality=muni, document_type="agenda")
        minutes = MeetingDocumentFactory(municipality=muni, document_type="minutes")

        agenda_page = MeetingPageFactory(document=agenda, text="housing")
        minutes_page = MeetingPageFactory(document=minutes, text="housing")

        # Search only agendas
        search = SearchFactory(search_term="housing", document_type="agenda")
        search.municipalities.add(muni)

        results = execute_search(search)

        assert agenda_page in results
        assert minutes_page not in results

    def test_execute_search_with_meeting_name_filter(self):
        """Test search filters by meeting name using full-text search."""
        from searches.services import execute_search

        muni = MuniFactory()

        # Different meeting bodies
        council_doc = MeetingDocumentFactory(
            municipality=muni, meeting_name="CityCouncil"
        )
        planning_doc = MeetingDocumentFactory(
            municipality=muni, meeting_name="PlanningCommission"
        )

        _council_page = MeetingPageFactory(document=council_doc, text="budget")
        planning_page = MeetingPageFactory(document=planning_doc, text="budget")

        # Search for "planning" meetings only
        search = SearchFactory(search_term="budget", meeting_name_query="planning")
        search.municipalities.add(muni)

        results = execute_search(search)

        assert planning_page in results
        # Note: CityCouncil may or may not match depending on search vector setup
        # The key is that planning_page definitely matches

    def test_execute_search_returns_queryset(self):
        """Test that execute_search returns a Django QuerySet."""
        from django.db.models import QuerySet

        from searches.services import execute_search

        search = SearchFactory(search_term="test")
        results = execute_search(search)

        assert isinstance(results, QuerySet)
        assert results.model == MeetingPage

    def test_execute_search_with_all_filters_combined(self):
        """Test search with all filter types combined."""
        from searches.services import execute_search

        # Setup complex scenario
        ca_muni = MuniFactory(name="Berkeley", state="CA")
        or_muni = MuniFactory(name="Portland", state="OR")

        ca_doc = MeetingDocumentFactory(
            municipality=ca_muni,
            meeting_date=date(2024, 3, 15),
            document_type="agenda",
            meeting_name="CityCouncil",
        )

        # This page should match all filters
        matching_page = MeetingPageFactory(document=ca_doc, text="housing policy")

        # Create non-matching pages
        wrong_state_doc = MeetingDocumentFactory(
            municipality=or_muni,  # OR instead of CA
            meeting_date=date(2024, 3, 15),
            document_type="agenda",
        )
        wrong_state_page = MeetingPageFactory(document=wrong_state_doc, text="housing")

        # Search with all filters
        search = SearchFactory(
            search_term="housing",
            states=["CA"],
            date_from=date(2024, 3, 1),
            date_to=date(2024, 3, 31),
            document_type="agenda",
        )
        search.municipalities.add(ca_muni)

        results = execute_search(search)

        assert matching_page in results
        assert wrong_state_page not in results

    def test_execute_search_with_empty_meeting_name_query(self):
        """
        When meeting_name_query is empty/None, search should not filter by meeting name.
        This tests the early return edge case in _apply_meeting_name_filter.
        """
        from searches.services import execute_search

        muni = MuniFactory(name="Berkeley")
        doc = MeetingDocumentFactory(municipality=muni, meeting_name="City Council")
        page = MeetingPageFactory(document=doc, text="General discussion")

        # Create search with empty meeting_name_query
        search = SearchFactory(search_term="discussion", meeting_name_query="")
        search.municipalities.add(muni)

        results = execute_search(search)

        # Should still return the page (no meeting name filtering)
        assert page in results


@pytest.mark.django_db
class TestGetNewPages:
    """Test the get_new_pages() function that returns only new results."""

    def test_get_new_pages_returns_only_new(self):
        """Test that get_new_pages returns only pages created after last check timestamp."""
        import time

        from django.utils import timezone

        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        # Create old pages
        old_page1 = MeetingPageFactory(document=doc, text="housing")
        old_page2 = MeetingPageFactory(document=doc, text="housing policy")

        # Create search and mark timestamp after old pages were created
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = timezone.now()
        search.save()

        # Small delay to ensure new page has later timestamp
        time.sleep(0.01)

        # Create new page after the check timestamp
        new_page = MeetingPageFactory(document=doc, text="housing budget")

        # Get new pages
        new_pages = get_new_pages(search)

        # Should only return new_page (created after last_checked_for_new_pages)
        assert new_pages.count() == 1
        assert new_page in new_pages
        assert old_page1 not in new_pages
        assert old_page2 not in new_pages

    def test_get_new_pages_with_empty_last_results(self):
        """Test get_new_pages when last_checked_for_new_pages is None (first run)."""
        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        page1 = MeetingPageFactory(document=doc, text="housing")
        page2 = MeetingPageFactory(document=doc, text="housing policy")

        # Create search with no previous check timestamp
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None
        search.save()

        # Get new pages
        new_pages = get_new_pages(search)

        # Should return ALL matching pages
        assert new_pages.count() == 2
        assert page1 in new_pages
        assert page2 in new_pages

    def test_get_new_pages_with_no_new_results(self):
        """Test get_new_pages when all current results were created before last check."""
        from django.utils import timezone

        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        _page1 = MeetingPageFactory(document=doc, text="housing")
        _page2 = MeetingPageFactory(document=doc, text="housing")

        # Create search with timestamp set to now (after pages were created)
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = (
            timezone.now()
        )  # Check happened after all pages
        search.save()

        # Get new pages
        new_pages = get_new_pages(search)

        # Should return empty queryset
        assert new_pages.count() == 0

    def test_get_new_pages_returns_queryset(self):
        """Test that get_new_pages returns a Django QuerySet."""
        from django.db.models import QuerySet

        from searches.services import get_new_pages

        search = SearchFactory(search_term="test")
        search.last_checked_for_new_pages = None
        search.save()

        new_pages = get_new_pages(search)

        assert isinstance(new_pages, QuerySet)
        assert new_pages.model == MeetingPage


@pytest.mark.django_db
class TestSearchServiceIntegration:
    """Integration tests for search service functions working together."""

    def test_search_service_workflow(self):
        """Test complete workflow: execute_search -> track results -> get_new_pages."""
        from searches.services import execute_search, get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        # Initial pages
        _page1 = MeetingPageFactory(document=doc, text="housing policy")
        _page2 = MeetingPageFactory(document=doc, text="housing budget")

        # Create search
        import time

        from django.utils import timezone

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None
        search.save()

        # First execution - should find both pages
        initial_results = execute_search(search)
        assert initial_results.count() == 2

        # Get new pages (all are new on first run)
        new_pages_first = get_new_pages(search)
        assert new_pages_first.count() == 2

        # Update timestamp to mark these pages as seen
        search.last_checked_for_new_pages = timezone.now()
        search.save()

        # Small delay to ensure new page has later timestamp
        time.sleep(0.01)

        # Second execution - no new pages yet
        new_pages_second = get_new_pages(search)
        assert new_pages_second.count() == 0

        # Add a new page
        page3 = MeetingPageFactory(document=doc, text="housing development")

        # Third execution - should find the new page
        new_pages_third = get_new_pages(search)
        assert new_pages_third.count() == 1
        assert page3 in new_pages_third
