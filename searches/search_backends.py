"""
Search backend abstraction layer.

This module provides a unified interface for searching meeting pages
via ParadeDB pg_search BM25.

All search operations are automatically cached using Redis to eliminate database load
for repeated queries.
"""

import hashlib
from abc import ABC, abstractmethod
from typing import Any

from django.core.cache import cache
from django.db import connection
from django.db.models import QuerySet

from .cache import get_cached_search_results, set_cached_search_results

# Match the tags meetings/views.py already used for ts_headline so templates
# and their |safe filters need no change.
HEADLINE_START_TAG = "<mark>"
HEADLINE_STOP_TAG = "</mark>"
SNIPPET_MAX_CHARS = 150


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
                muni_ids = list(municipalities.values_list("id", flat=True))  # pyright: ignore[reportAttributeAccessIssue]
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


# Above this many matches the UI shows "{COUNT_CAP}+" instead of an exact total.
# An exact count means enumerating the whole match set (~1.9s for a 380k-hit
# term); a LIMIT-capped count lets the index scan stop early and stays fast.
COUNT_CAP = 1000


class PgSearchBackend(SearchBackend):  # noqa: F821 — defined in search_backends.py
    """BM25 search over meetings_meetingpage via ParadeDB pg_search."""

    def get_backend_name(self) -> str:
        return "pg_search"

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
        where: list[str] = []
        params: list[Any] = []

        # --- full-text predicates ------------------------------------------
        # Tantivy query-string language via paradedb.parse(): bare terms are
        # OR'd by default, AND/OR/NOT keywords and `-term` negation are native,
        # and quoted blocks become positional phrases. `@@@` matches the index
        # key field (id) against the parsed query.
        if query_text:
            where.append("id @@@ paradedb.parse(%s)")
            params.append(f"text:({query_text.strip()})")

        if meeting_name_query:
            # Raw meeting_name only. CamelCase splitting lives in a separate
            # meeting_name_alias lookup table (post-migration) rather than as a
            # column here — see meetingpage_model_changes.py for why.
            where.append("id @@@ paradedb.parse(%s)")
            params.append(f"meeting_name:({meeting_name_query.strip()})")

        # --- filters: all local columns now, no joins ----------------------
        if municipalities is not None:
            muni_ids = (
                list(municipalities.values_list("id", flat=True))
                if hasattr(municipalities, "values_list")
                else [m.id if hasattr(m, "id") else m for m in municipalities]
            )
            if muni_ids:
                where.append("municipality_id = ANY(%s)")
                params.append(muni_ids)

        if states:
            where.append("state = ANY(%s)")
            params.append(list(states))

        if date_from:
            where.append("meeting_date >= %s")
            params.append(date_from)

        if date_to:
            where.append("meeting_date <= %s")
            params.append(date_to)

        if document_type and document_type != "all":
            where.append("document_type = %s")
            params.append(document_type)

        if not where:
            # No query and no filters: fall back to a plain recency listing
            # rather than asking BM25 to rank the entire corpus.
            where.append("TRUE")

        where_sql = " AND ".join(where)

        # Relevance ordering only makes sense when there's a query to score.
        order_sql = (
            "ORDER BY paradedb.score(id) DESC, meeting_date DESC"
            if query_text
            else "ORDER BY meeting_date DESC, id"
        )

        # paradedb.snippet() requires a ParadeDB operator in the same query, so it
        # can only be selected when there IS a text query. This replaces the
        # old two-pass design (search, then _generate_headlines_for_page over
        # the paginated slice) — one query instead of two, and the highlighting
        # is guaranteed to match what BM25 actually matched.
        #
        # Snippets are expensive, which is why this is only ever run against
        # the LIMITed page, never the full result set.
        if query_text:
            snippet_sql = (
                "paradedb.snippet(text, start_tag => %s, end_tag => %s, "
                "max_num_chars => %s) AS snippet"
            )
            snippet_params = [HEADLINE_START_TAG, HEADLINE_STOP_TAG, SNIPPET_MAX_CHARS]
        else:
            snippet_sql = "NULL AS snippet"
            snippet_params = []

        sql = f"""
            SELECT id, document_id, page_number, text, page_image,
                   municipality_id, municipality_subdomain, municipality_name,
                   state, meeting_name, meeting_date, document_type,
                   {snippet_sql}
            FROM meetings_meetingpage
            WHERE {where_sql}
            {order_sql}
            LIMIT %s OFFSET %s
        """
        # Snippet params bind in the SELECT list, which precedes WHERE.
        row_params: list[Any] = snippet_params + params + [limit, offset]

        with connection.cursor() as cur:
            cur.execute(sql, row_params)
            rows = cur.fetchall()

        if not rows:
            return [], 0

        # --- total_count, as a SEPARATE query --------------------------------
        # NEVER use count(*) OVER () in the query above. It forces ParadeDB to
        # enumerate the ENTIRE match set to count it, discarding the top-K
        # optimization: measured 47ms -> 1219ms (26x) on a 380k-hit query, with
        # 377x more buffers read. A standalone count keeps the fast path.
        #
        # ParadeDB counts a bare `text ||| %s` match quickly via the index, but
        # once other filters are ANDed in it can still be the slow part of the
        # request. So we cache it, keyed by the WHERE + params — pagination
        # reuses one count across all pages of the same search.
        total = self._count(where_sql, params)

        return [self._row_to_dict(r) for r in rows], total

    def _count(self, where_sql: str, params: list) -> int:
        """Cached, CAPPED count for a given filter set.

        An exact count is deliberately not computed. ParadeDB's aggregate scan
        still reads the entire match set to count it — measured 1.87s / 239k
        buffers for a 380k-hit term — which is too slow to run per search even
        once. Instead we count only up to COUNT_CAP+1 rows via a LIMIT
        subquery, which lets the top-K index scan stop early. The UI shows
        "1000+ results" past the cap, which is all a search header needs.

        Returned value:
          - exact when the match set is <= COUNT_CAP
          - COUNT_CAP + 1 as a sentinel meaning "at least this many"
        Callers/templates render `>= COUNT_CAP` as "{COUNT_CAP}+".
        """
        cache_key = (
            "pgsearch:count:"
            + hashlib.sha1((where_sql + repr(params)).encode()).hexdigest()
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Counting a LIMITed subquery lets the index scan stop at the cap
        # instead of enumerating all matches. Fast regardless of match-set size.
        sql = f"""
            SELECT count(*) FROM (
                SELECT 1 FROM meetings_meetingpage
                WHERE {where_sql}
                LIMIT {COUNT_CAP + 1}
            ) capped
        """
        with connection.cursor() as cur:
            cur.execute(sql, params)
            total = cur.fetchone()[0]

        cache.set(cache_key, total, 300)
        return total

    @staticmethod
    def _row_to_dict(r) -> dict[str, Any]:
        """Must match the dict shape the old backends returned — this contract
        is what makes the backend swap invisible to views, services, and the
        digest pipeline. All keys, same names."""
        return {
            "id": r[0],
            "document_id": str(r[1]),
            "page_number": r[2],
            "text": r[3],
            "page_image": r[4],
            "municipality_id": str(r[5]),
            "subdomain": r[6],
            "municipality_name": r[7],
            "state": r[8],
            "meeting_name": r[9],
            "meeting_date": r[10],
            "document_type": r[11],
            "snippet": r[12],
            # Templates call result.document.civic_band_table_name to build
            # civic.band URLs. document_type is denormalized now, so derive it
            # here rather than joining back for a two-branch property.
            "civic_band_table_name": "agendas" if r[11] == "agenda" else "minutes",
        }


def get_search_backend() -> SearchBackend:
    """Get the search backend. Only one backend exists now."""
    return PgSearchBackend()
