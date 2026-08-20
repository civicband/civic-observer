"""
Shared fixtures for search tests.

Since PgSearchBackend uses pg_search operators (|||, pdb.score, pdb.snippet)
that aren't available in test databases, we provide a mock backend that uses
Django ORM icontains as a fallback for full-text matching.
"""

from unittest.mock import MagicMock, patch

import pytest

from meetings.models import MeetingPage


def _build_mock_backend():
    """Build a mock backend that simulates search using Django ORM."""
    mock_backend = MagicMock()

    def search_side_effect(**kwargs):
        qs = MeetingPage.objects.all()
        query_text = kwargs.get("query_text", "")
        municipalities = kwargs.get("municipalities")
        states = kwargs.get("states")
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        document_type = kwargs.get("document_type")
        meeting_name_query = kwargs.get("meeting_name_query")
        limit = kwargs.get("limit", 100)
        offset = kwargs.get("offset", 0)

        if query_text:
            qs = qs.filter(text__icontains=query_text)
        if meeting_name_query:
            qs = qs.filter(meeting_name__icontains=meeting_name_query)
        if municipalities is not None:
            if hasattr(municipalities, "values_list"):
                muni_ids = list(municipalities.values_list("id", flat=True))
            else:
                muni_ids = [m.id if hasattr(m, "id") else m for m in municipalities]
            if muni_ids:
                qs = qs.filter(municipality_id__in=muni_ids)
        if states:
            qs = qs.filter(state__in=states)
        if date_from:
            qs = qs.filter(meeting_date__gte=date_from)
        if date_to:
            qs = qs.filter(meeting_date__lte=date_to)
        if document_type and document_type != "all":
            qs = qs.filter(document_type=document_type)

        total = qs.count()
        page_slice = qs[offset : offset + limit]
        results = [{"id": p.id} for p in page_slice]
        return results, total

    mock_backend.search.side_effect = search_side_effect
    mock_backend.search_with_cache.side_effect = search_side_effect
    return mock_backend


@pytest.fixture(autouse=True)
def mock_search_backend():
    """Auto-mock the search backend for all tests in this directory.

    Tests that need to test the actual backend (e.g., test_pg_search_backend.py)
    mock the cursor directly and don't go through get_search_backend(), so this
    fixture doesn't interfere with them.
    """
    with patch(
        "searches.search_backends.get_search_backend",
        return_value=_build_mock_backend(),
    ) as mock_get:
        yield mock_get
