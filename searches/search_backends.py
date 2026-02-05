"""
Search backend abstraction layer.

This module provides a unified interface for searching meeting pages,
with support for multiple backends (PostgreSQL, Meilisearch).

The backend is selected via SEARCH_BACKEND setting, with PostgreSQL as the default fallback.
"""

from abc import ABC, abstractmethod
from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, QuerySet

from meetings.models import MeetingPage

from .meilisearch_client import get_meeting_pages_index
from .services import _get_smart_threshold, _parse_websearch_query


class SearchBackend(ABC):
    """Abstract base class for search backends."""

    @abstractmethod
    def search(
        self,
        query_text: str,
        municipalities: QuerySet | list | None = None,
        states: list | None = None,
        date_from=None,
        date_to=None,
        document_type: str | None = None,
        meeting_name_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Execute a search with filters.

        Returns:
            Tuple of (results, total_count)
            - results: List of dictionaries with page data
            - total_count: Total number of matching results (for pagination)
        """
        pass

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return the name of this backend."""
        pass


class PostgresSearchBackend(SearchBackend):
    """PostgreSQL full-text search backend using existing implementation."""

    def get_backend_name(self) -> str:
        return "postgres"

    def search(
        self,
        query_text: str,
        municipalities: QuerySet | list | None = None,
        states: list | None = None,
        date_from=None,
        date_to=None,
        document_type: str | None = None,
        meeting_name_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Search using PostgreSQL full-text search.

        Uses the existing search_vector field and GIN indexes.
        """
        # Import here to avoid circular dependency
        from .services import _apply_meeting_name_filter, _apply_search_filters

        # Start with all pages
        queryset = MeetingPage.objects.select_related(
            "document", "document__municipality"
        ).all()

        # Apply filters
        queryset = _apply_search_filters(
            queryset,
            municipalities=municipalities,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
        )

        # Apply meeting name filter
        if meeting_name_query:
            queryset = _apply_meeting_name_filter(queryset, meeting_name_query)

        # Apply full-text search if query provided
        if query_text:
            queryset = self._apply_full_text_search(queryset, query_text)
        else:
            # All updates mode - order by date descending
            queryset = queryset.order_by("-document__meeting_date")

        # Get total count before pagination
        total_count = queryset.count()

        # Apply pagination
        results_queryset = queryset[offset : offset + limit]

        # Convert to dictionaries
        results = []
        for page in results_queryset:
            results.append(self._page_to_dict(page))

        return results, total_count

    def _apply_full_text_search(self, queryset: QuerySet, query_text: str) -> QuerySet:
        """Apply PostgreSQL full-text search with smart thresholds."""
        tokens, original_query = _parse_websearch_query(query_text)
        threshold = _get_smart_threshold(tokens)

        search_query = SearchQuery(
            original_query, search_type="websearch", config="simple"
        )

        queryset = (
            queryset.filter(search_vector=search_query)
            .annotate(
                rank=SearchRank(F("search_vector"), search_query),
            )
            .filter(rank__gte=threshold)
            .order_by("-rank", "-document__meeting_date")
        )

        return queryset

    def _page_to_dict(self, page: MeetingPage) -> dict[str, Any]:
        """Convert a MeetingPage object to a dictionary."""
        return {
            "id": page.id,
            "page_number": page.page_number,
            "text": page.text,
            "page_image": page.page_image,
            "meeting_name": page.document.meeting_name,
            "meeting_date": page.document.meeting_date.isoformat(),
            "document_type": page.document.document_type,
            "municipality_id": str(page.document.municipality_id),
            "municipality_subdomain": page.document.municipality.subdomain,
            "municipality_name": page.document.municipality.name,
            "state": page.document.municipality.state,
            "document_id": str(page.document.id),
        }


class MeilisearchBackend(SearchBackend):
    """Meilisearch backend for fast, typo-tolerant search."""

    def get_backend_name(self) -> str:
        return "meilisearch"

    def search(
        self,
        query_text: str,
        municipalities: QuerySet | list | None = None,
        states: list | None = None,
        date_from=None,
        date_to=None,
        document_type: str | None = None,
        meeting_name_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Search using Meilisearch.

        Builds filter expressions and executes search with Meilisearch API.
        """
        index = get_meeting_pages_index()

        # Build filter expression
        filters = self._build_filters(
            municipalities=municipalities,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
        )

        # Prepare search options
        search_options: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }

        if filters:
            search_options["filter"] = filters

        # Sort by date descending (most recent first)
        search_options["sort"] = ["meeting_date:desc"]

        # Execute search
        results = index.search(query_text or "", search_options)

        # Extract hits and total count
        hits = results.get("hits", [])
        total_count = results.get("estimatedTotalHits", 0)

        return hits, total_count

    def _build_filters(
        self,
        municipalities: QuerySet | list | None = None,
        states: list | None = None,
        date_from=None,
        date_to=None,
        document_type: str | None = None,
        meeting_name_query: str | None = None,
    ) -> str | None:
        """
        Build Meilisearch filter expression.

        Meilisearch uses a special filter syntax:
        - AND: field = value AND field2 = value2
        - OR: field = value OR field = value2
        - IN: field IN [value1, value2]
        - Comparison: field >= value, field <= value

        Returns:
            Filter string or None if no filters
        """
        filter_parts = []

        # Municipality filter
        if municipalities:
            if hasattr(municipalities, "values_list"):
                muni_ids = list(municipalities.values_list("id", flat=True))
            else:
                muni_ids = [
                    str(m.id) if hasattr(m, "id") else str(m) for m in municipalities
                ]

            if muni_ids:
                # Convert UUIDs to strings for Meilisearch
                muni_ids_str = [str(mid) for mid in muni_ids]
                muni_filter = " OR ".join(
                    [f'municipality_id = "{mid}"' for mid in muni_ids_str]
                )
                filter_parts.append(f"({muni_filter})")

        # State filter
        if states:
            state_filter = " OR ".join([f'state = "{state}"' for state in states])
            filter_parts.append(f"({state_filter})")

        # Date filters
        if date_from:
            date_str = (
                date_from.isoformat()
                if hasattr(date_from, "isoformat")
                else str(date_from)
            )
            filter_parts.append(f'meeting_date >= "{date_str}"')

        if date_to:
            date_str = (
                date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
            )
            filter_parts.append(f'meeting_date <= "{date_str}"')

        # Document type filter
        if document_type and document_type != "all":
            filter_parts.append(f'document_type = "{document_type}"')

        # Meeting name query filter (substring search)
        if meeting_name_query:
            # For meeting name queries, we'll include it in the main search query
            # and let Meilisearch's searchable attributes handle it
            # Alternatively, you could add a filter like:
            # filter_parts.append(f'meeting_name CONTAINS "{meeting_name_query}"')
            # But Meilisearch doesn't support CONTAINS in filters by default
            pass

        # Combine all filters with AND
        if not filter_parts:
            return None

        return " AND ".join(filter_parts)


def get_search_backend() -> SearchBackend:
    """
    Get the configured search backend.

    Returns the backend specified in SEARCH_BACKEND setting,
    falling back to PostgreSQL if the setting is invalid.

    Returns:
        SearchBackend instance
    """
    backend_name = getattr(settings, "SEARCH_BACKEND", "postgres")

    if backend_name == "meilisearch":
        return MeilisearchBackend()
    else:
        # Default to Postgres
        return PostgresSearchBackend()
