"""
Search backend abstraction layer.

This module provides a unified interface for searching meeting pages,
with support for multiple backends (PostgreSQL, Meilisearch).

The backend is selected via SEARCH_BACKEND setting, with PostgreSQL as the default fallback.

All search operations are automatically cached using Redis to eliminate database load
for repeated queries.
"""

from abc import ABC, abstractmethod
from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchQuery
from django.db.models import QuerySet

from meetings.models import MeetingPage

from .cache import get_cached_search_results, set_cached_search_results
from .meilisearch_client import get_meeting_pages_index
from .quickwit_client import execute_search_elasticsearch_compat


class SearchBackend(ABC):
    """Abstract base class for search backends."""

    def search_with_cache(
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
        Execute a search with Redis caching.

        Wraps the search() method with cache lookup/storage logic.
        Cache hit rate typically 80-90% for popular queries.

        Returns:
            Tuple of (results, total_count)
        """
        # Convert municipalities to list of IDs for cache key
        muni_ids = []
        if municipalities:
            if hasattr(municipalities, "values_list"):
                muni_ids = list(municipalities.values_list("id", flat=True))
            else:
                muni_ids = [m.id if hasattr(m, "id") else m for m in municipalities]

        # Convert dates to ISO strings for cache key
        date_from_str = date_from.isoformat() if date_from else None
        date_to_str = date_to.isoformat() if date_to else None

        # Try cache first
        cached = get_cached_search_results(
            search_term=query_text,
            municipalities=muni_ids,
            states=states or [],
            date_from=date_from_str,
            date_to=date_to_str,
            document_type=document_type or "all",
            meeting_name_query=meeting_name_query or "",
            limit=limit,
            offset=offset,
        )

        if cached is not None:
            return cached

        # Cache miss - execute search
        results, total = self.search(
            query_text=query_text,
            municipalities=municipalities,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
            limit=limit,
            offset=offset,
        )

        # Cache the results (5 minute TTL)
        set_cached_search_results(
            results=results,
            total_count=total,
            search_term=query_text,
            municipalities=muni_ids,
            states=states or [],
            date_from=date_from_str,
            date_to=date_to_str,
            document_type=document_type or "all",
            meeting_name_query=meeting_name_query or "",
            limit=limit,
            offset=offset,
            timeout=300,  # 5 minutes
        )

        return results, total

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

        This is the backend-specific implementation method.
        Most callers should use search_with_cache() instead.

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

        # Avoid expensive COUNT(*) — fetch limit+1 rows to detect if there are more
        results_queryset = queryset[offset : offset + limit + 1]

        # Convert to dictionaries
        results = []
        for page in results_queryset:
            results.append(self._page_to_dict(page))

        # If we got more than limit results, there are more pages
        has_more = len(results) > limit
        if has_more:
            results = results[:limit]

        # Return a count that's useful for pagination without scanning full result set:
        # - If fewer results than limit, we know the exact total
        # - If more, report offset + limit + 1 to signal "there are more"
        if has_more:
            total_count = offset + limit + 1
        else:
            total_count = offset + len(results)

        return results, total_count

    def _apply_full_text_search(self, queryset: QuerySet, query_text: str) -> QuerySet:
        """Apply PostgreSQL full-text search optimized for speed."""
        search_query = SearchQuery(query_text, search_type="websearch", config="simple")

        # Use only GIN index (@@ operator) without expensive ts_rank computation
        # This is dramatically faster - no rank computation needed
        queryset = queryset.filter(search_vector=search_query).order_by(
            "-document__meeting_date"
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


class QuickwitBackend(SearchBackend):
    """
    Quickwit backend for full-text search on S3-backed storage.

    Quickwit stores its index on Fastly Object Storage (S3-compatible),
    making it cost-efficient for large document collections (10M-100M+).
    """

    def get_backend_name(self) -> str:
        return "quickwit"

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
        Search using Quickwit's Elasticsearch-compatible API.
        """
        es_query = self._build_query(
            query_text=query_text,
            municipalities=municipalities,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
            limit=limit,
            offset=offset,
        )

        result = execute_search_elasticsearch_compat(
            query_text=query_text,
            limit=limit,
            offset=offset,
            filters=es_query.get("filters"),
            should=es_query.get("should"),
            sort_by=[{"meeting_date": "desc"}],
        )

        hits = result.get("hits", {}).get("hits", [])
        total = result.get("hits", {}).get("total", {}).get("value", 0)

        results = []
        for hit in hits:
            source = hit.get("_source", hit)
            results.append(self._hit_to_dict(source))

        return results, total

    def _build_query(
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
    ) -> dict[str, Any]:
        """
        Build Quickwit-compatible query filters.
        """
        filters: list[dict[str, Any]] = []

        if municipalities:
            if hasattr(municipalities, "values_list"):
                muni_ids = list(municipalities.values_list("id", flat=True))
            else:
                muni_ids = [m.id if hasattr(m, "id") else m for m in municipalities]

            if muni_ids:
                muni_filter = {
                    "terms": {"municipality_id": [str(mid) for mid in muni_ids]}
                }
                filters.append(muni_filter)

        if states:
            state_filter = {"terms": {"state": states}}
            filters.append(state_filter)

        if date_from:
            date_str = (
                date_from.isoformat()
                if hasattr(date_from, "isoformat")
                else str(date_from)
            )
            filters.append({"range": {"meeting_date": {"gte": date_str}}})

        if date_to:
            date_str = (
                date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
            )
            filters.append({"range": {"meeting_date": {"lte": date_str}}})

        if document_type and document_type != "all":
            filters.append({"term": {"document_type": document_type}})

        should_clauses: list[dict[str, Any]] = []
        if meeting_name_query:
            should_clauses.append(
                {
                    "query_string": {
                        "query": meeting_name_query,
                        "fields": ["meeting_name"],
                    }
                }
            )

        result: dict[str, Any] = {}
        if should_clauses and query_text:
            should_clauses.append(
                {"query_string": {"query": query_text, "fields": ["text"]}}
            )
            result["should"] = should_clauses
        elif query_text:
            result["main_query"] = query_text
        elif should_clauses:
            result["should"] = should_clauses

        if filters:
            result["filters"] = filters

        return result

    def _hit_to_dict(self, hit: dict[str, Any]) -> dict[str, Any]:
        """Convert a Quickwit ES-compatible hit to a standardized result dictionary.

        Quickwit 0.8 with store_source=true wraps documents as _source._source,
        so we need to unwrap the inner document.
        """
        source = hit.get("_source", hit)
        # Unwrap the double-nested source if present
        inner = source.get("_source", source)
        return {
            "id": inner.get("id", ""),
            "page_number": inner.get("page_number", 0),
            "text": inner.get("text", ""),
            "page_image": inner.get("page_image", ""),
            "meeting_name": inner.get("meeting_name", ""),
            "meeting_date": inner.get("meeting_date", ""),
            "document_type": inner.get("document_type", ""),
            "municipality_id": inner.get("municipality_id", ""),
            "municipality_subdomain": inner.get("municipality_subdomain", ""),
            "municipality_name": inner.get("municipality_name", ""),
            "state": inner.get("state", ""),
            "document_id": inner.get("document_id", ""),
        }


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
    elif backend_name == "quickwit":
        return QuickwitBackend()
    else:
        # Default to Postgres
        return PostgresSearchBackend()
