"""
Tests for searches.services module - shared search service functions.

These tests verify the behavior of execute_search() and get_new_pages().
Since PgSearchBackend uses raw SQL with pg_search operators (|||) that aren't
available in test databases, we mock the backend's search() method.
"""

from datetime import date
from unittest.mock import patch

import pytest

from meetings.models import MeetingPage
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    SearchFactory,
)


def _mock_search_returning_ids(page_ids):
    """Helper to create a mock search result returning specific page IDs."""
    results = [{"id": pid} for pid in page_ids]
    return results, len(results)


@pytest.mark.django_db
class TestExecuteSearch:
    """Test the execute_search() function that queries local MeetingPage database."""

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_with_text_query(self, mock_get_backend):
        """Test basic text search returns matching pages."""
        from searches.services import execute_search

        muni = MuniFactory(name="Berkeley")
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="Discussion about housing policy")
        page2 = MeetingPageFactory(document=doc, text="Budget allocation for housing")
        page3 = MeetingPageFactory(document=doc, text="Zoning changes")

        # Mock backend to return page1 and page2 (matching "housing")
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id]
        )

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)

        results = execute_search(search)

        assert results is not None
        assert page1 in results
        assert page2 in results
        assert page3 not in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_empty_query_returns_all(self, mock_get_backend):
        """Test that empty search_term returns all pages (all updates mode)."""
        from searches.services import execute_search

        muni = MuniFactory(name="Oakland")
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="Housing policy")
        page2 = MeetingPageFactory(document=doc, text="Budget report")
        page3 = MeetingPageFactory(document=doc, text="Zoning changes")

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id, page3.id]
        )

        search = SearchFactory(search_term="")
        search.municipalities.add(muni)

        results = execute_search(search)

        assert results.count() == 3
        assert page1 in results
        assert page2 in results
        assert page3 in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_with_date_filters(self, mock_get_backend):
        """Test search filters by date range."""
        from searches.services import execute_search

        muni = MuniFactory()

        doc_jan = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 1, 15)
        )
        doc_mar = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 3, 15)
        )
        doc_may = MeetingDocumentFactory(
            municipality=muni, meeting_date=date(2024, 5, 15)
        )

        _page_jan = MeetingPageFactory(document=doc_jan, text="budget")
        page_mar = MeetingPageFactory(document=doc_mar, text="budget")
        _page_may = MeetingPageFactory(document=doc_may, text="budget")

        # Mock: backend only returns the March page (date-filtered)
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids([page_mar.id])

        search = SearchFactory(
            search_term="budget",
            date_from=date(2024, 2, 1),
            date_to=date(2024, 4, 30),
        )
        search.municipalities.add(muni)

        results = execute_search(search)

        assert results.count() == 1
        assert page_mar in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_with_multiple_municipalities(self, mock_get_backend):
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

        # Mock: backend only returns pages from muni1 and muni2
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id]
        )

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni1, muni2)

        results = execute_search(search)

        assert page1 in results
        assert page2 in results
        assert page3 not in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_with_states_filter(self, mock_get_backend):
        """Test search filters by state."""
        from searches.services import execute_search

        ca_muni = MuniFactory(name="Berkeley", state="CA")
        or_muni = MuniFactory(name="Portland", state="OR")

        ca_doc = MeetingDocumentFactory(municipality=ca_muni)
        or_doc = MeetingDocumentFactory(municipality=or_muni)

        ca_page = MeetingPageFactory(document=ca_doc, text="budget")
        or_page = MeetingPageFactory(document=or_doc, text="budget")

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids([ca_page.id])

        search = SearchFactory(search_term="budget", states=["CA"])

        results = execute_search(search)

        assert ca_page in results
        assert or_page not in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_with_document_type_filter(self, mock_get_backend):
        """Test search filters by document type (agenda/minutes)."""
        from searches.services import execute_search

        muni = MuniFactory()

        agenda = MeetingDocumentFactory(municipality=muni, document_type="agenda")
        minutes = MeetingDocumentFactory(municipality=muni, document_type="minutes")

        agenda_page = MeetingPageFactory(document=agenda, text="housing")
        minutes_page = MeetingPageFactory(document=minutes, text="housing")

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids([agenda_page.id])

        search = SearchFactory(search_term="housing", document_type="agenda")
        search.municipalities.add(muni)

        results = execute_search(search)

        assert agenda_page in results
        assert minutes_page not in results

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_returns_queryset(self, mock_get_backend):
        """Test that execute_search returns a Django QuerySet."""
        from django.db.models import QuerySet

        from searches.services import execute_search

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([], 0)

        search = SearchFactory(search_term="test")
        results = execute_search(search)

        assert isinstance(results, QuerySet)
        assert results.model == MeetingPage

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_no_results(self, mock_get_backend):
        """Test that execute_search returns empty queryset when no results."""
        from searches.services import execute_search

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([], 0)

        search = SearchFactory(search_term="nonexistent")
        results = execute_search(search)

        assert results.count() == 0

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_passes_correct_params_to_backend(self, mock_get_backend):
        """Test that execute_search passes search params to backend correctly."""
        from searches.services import execute_search

        muni = MuniFactory()
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([], 0)

        search = SearchFactory(
            search_term="housing",
            states=["CA"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
            meeting_name_query="council",
        )
        search.municipalities.add(muni)

        execute_search(search)

        mock_backend.search.assert_called_once()
        call_kwargs = mock_backend.search.call_args.kwargs
        assert call_kwargs["query_text"] == "housing"
        assert call_kwargs["states"] == ["CA"]
        assert call_kwargs["date_from"] == date(2024, 1, 1)
        assert call_kwargs["date_to"] == date(2024, 12, 31)
        assert call_kwargs["document_type"] == "agenda"
        assert call_kwargs["meeting_name_query"] == "council"
        assert call_kwargs["limit"] == 10000

    @patch("searches.search_backends.get_search_backend")
    def test_execute_search_logs_warning_at_cap(self, mock_get_backend, caplog):
        """Test that hitting the 10,000 result cap logs a warning."""
        import logging

        from searches.services import execute_search

        mock_backend = mock_get_backend.return_value
        # Return exactly 10,000 results
        mock_backend.search.return_value = (
            [{"id": f"page-{i}"} for i in range(10000)],
            10000,
        )

        search = SearchFactory(search_term="common")

        with caplog.at_level(logging.WARNING):
            execute_search(search)

        assert "10,000 result cap" in caplog.text


@pytest.mark.django_db
class TestGetNewPages:
    """Test the get_new_pages() function that returns only new results."""

    @patch("searches.search_backends.get_search_backend")
    def test_get_new_pages_returns_only_new(self, mock_get_backend):
        """Test that get_new_pages returns only pages created after last check timestamp."""
        import time

        from django.utils import timezone

        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        old_page1 = MeetingPageFactory(document=doc, text="housing")
        old_page2 = MeetingPageFactory(document=doc, text="housing policy")

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = timezone.now()
        search.save()

        time.sleep(0.01)

        new_page = MeetingPageFactory(document=doc, text="housing budget")

        # Mock backend returns all three pages
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [old_page1.id, old_page2.id, new_page.id]
        )

        new_pages = get_new_pages(search)

        assert new_pages.count() == 1
        assert new_page in new_pages
        assert old_page1 not in new_pages
        assert old_page2 not in new_pages

    @patch("searches.search_backends.get_search_backend")
    def test_get_new_pages_with_no_last_check(self, mock_get_backend):
        """Test get_new_pages when last_checked_for_new_pages is None (first run)."""
        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        page1 = MeetingPageFactory(document=doc, text="housing")
        page2 = MeetingPageFactory(document=doc, text="housing policy")

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None
        search.save()

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id]
        )

        new_pages = get_new_pages(search)

        assert new_pages.count() == 2
        assert page1 in new_pages
        assert page2 in new_pages

    @patch("searches.search_backends.get_search_backend")
    def test_get_new_pages_with_no_new_results(self, mock_get_backend):
        """Test get_new_pages when all results were created before last check."""
        from django.utils import timezone

        from searches.services import get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        page1 = MeetingPageFactory(document=doc, text="housing")
        page2 = MeetingPageFactory(document=doc, text="housing")

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = timezone.now()
        search.save()

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id]
        )

        new_pages = get_new_pages(search)

        assert new_pages.count() == 0

    @patch("searches.search_backends.get_search_backend")
    def test_get_new_pages_returns_queryset(self, mock_get_backend):
        """Test that get_new_pages returns a Django QuerySet."""
        from django.db.models import QuerySet

        from searches.services import get_new_pages

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([], 0)

        search = SearchFactory(search_term="test")
        search.last_checked_for_new_pages = None
        search.save()

        new_pages = get_new_pages(search)

        assert isinstance(new_pages, QuerySet)
        assert new_pages.model == MeetingPage


@pytest.mark.django_db
class TestSearchServiceIntegration:
    """Integration tests for search service functions working together."""

    @patch("searches.search_backends.get_search_backend")
    def test_search_service_workflow(self, mock_get_backend):
        """Test complete workflow: execute_search -> track results -> get_new_pages."""
        from searches.services import execute_search, get_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)

        page1 = MeetingPageFactory(document=doc, text="housing policy")
        page2 = MeetingPageFactory(document=doc, text="housing budget")

        import time

        from django.utils import timezone

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None
        search.save()

        # First execution - backend returns both pages
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id]
        )

        initial_results = execute_search(search)
        assert initial_results.count() == 2

        new_pages_first = get_new_pages(search)
        assert new_pages_first.count() == 2

        search.last_checked_for_new_pages = timezone.now()
        search.save()

        time.sleep(0.01)

        # Second execution - no new pages yet
        new_pages_second = get_new_pages(search)
        assert new_pages_second.count() == 0

        # Add a new page
        page3 = MeetingPageFactory(document=doc, text="housing development")

        # Backend now returns all three
        mock_backend.search.return_value = _mock_search_returning_ids(
            [page1.id, page2.id, page3.id]
        )

        new_pages_third = get_new_pages(search)
        assert new_pages_third.count() == 1
        assert page3 in new_pages_third
