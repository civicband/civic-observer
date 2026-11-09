"""Canonical, immutable description of a meeting-page search.

Every query path that claims to show "the same results" — the interactive
search, alerting, and Research set materialization — builds one of these and
then calls :func:`build_where`. The frozen JSON form is also what Research
stores on a set and hashes into ``query_hash``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


@dataclass(frozen=True)
class QuerySpec:
    text: str = ""
    meeting_name: str = ""
    municipality_ids: tuple[uuid.UUID, ...] = ()
    states: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = "all"
    modified_after: datetime | None = None

    def canonical(self) -> dict:
        """Return a normalized dict suitable for storage and hashing.

        Ordering of municipalities and states is removed so the same logical
        query always produces the same hash.
        """
        return {
            "text": _clean(self.text),
            "meeting_name": _clean(self.meeting_name),
            "municipality_ids": sorted(
                str(muni_id) for muni_id in self.municipality_ids
            ),
            "states": sorted({state for state in self.states if state}),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "document_type": self.document_type or "all",
            "modified_after": (
                self.modified_after.isoformat() if self.modified_after else None
            ),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))

    def query_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @classmethod
    def from_canonical(cls, data: dict) -> QuerySpec:
        return cls(
            text=data.get("text", ""),
            meeting_name=data.get("meeting_name", ""),
            municipality_ids=tuple(
                uuid.UUID(value) for value in data.get("municipality_ids", [])
            ),
            states=tuple(data.get("states", [])),
            date_from=(
                date.fromisoformat(data["date_from"]) if data.get("date_from") else None
            ),
            date_to=date.fromisoformat(data["date_to"])
            if data.get("date_to")
            else None,
            document_type=data.get("document_type", "all"),
            modified_after=(
                datetime.fromisoformat(data["modified_after"])
                if data.get("modified_after")
                else None
            ),
        )

    @classmethod
    def from_search(
        cls, search, *, modified_after: datetime | None = None
    ) -> QuerySpec:
        return cls(
            text=_clean(search.search_term),
            meeting_name=_clean(search.meeting_name_query),
            municipality_ids=tuple(
                sorted(search.municipalities.values_list("id", flat=True), key=str)
            ),
            states=tuple(search.states or ()),
            date_from=search.date_from,
            date_to=search.date_to,
            document_type=search.document_type or "all",
            modified_after=modified_after,
        )

    def with_modified_after(self, cutoff: datetime | None) -> QuerySpec:
        return replace(self, modified_after=cutoff)


def _column(alias: str, name: str) -> str:
    return f"{alias}.{name}" if alias else name


def _quote_bm25(value: str) -> str:
    """Quote a filter value for the ParadeDB query string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _or_terms(field: str, values) -> str | None:
    """Render one BM25 field filter, ORing multiple values."""
    rendered = [f"{field}:{_quote_bm25(str(value))}" for value in values if str(value)]
    if not rendered:
        return None
    joined = " OR ".join(rendered)
    return f"({joined})" if len(rendered) > 1 else joined


def _bm25_query(spec: QuerySpec) -> str | None:
    """Build one ParadeDB query string with the indexed filters pushed down.

    Separate SQL predicates on indexed columns (`state = ANY(...)`, date
    comparisons, document type) are not pushed into the BM25 index scan and
    force a full match enumeration. Gate A measured 20s for a California
    "housing" count with SQL filters vs 0.44s with field-qualified BM25
    filters. `modified` is not in the index, so it stays a SQL predicate.
    """
    # Filter-only queries keep plain SQL predicates: the test database and
    # dev environments may not have pg_search installed. Text queries already
    # require it, so folding filters into the BM25 query there is free.
    if not (spec.text or spec.meeting_name):
        return None

    clauses: list[str] = []

    if spec.text:
        clauses.append(f"text:({_clean(spec.text)})")
    if spec.meeting_name:
        clauses.append(f"meeting_name:({_clean(spec.meeting_name)})")

    municipality_clause = _or_terms("municipality_id", spec.municipality_ids)
    if municipality_clause:
        clauses.append(municipality_clause)

    state_clause = _or_terms("state", spec.states)
    if state_clause:
        clauses.append(state_clause)

    if spec.date_from or spec.date_to:
        start = spec.date_from.isoformat() if spec.date_from else "*"
        end = spec.date_to.isoformat() if spec.date_to else "*"
        clauses.append(f"meeting_date:[{start} TO {end}]")

    if spec.document_type and spec.document_type != "all":
        clauses.append(f"document_type:{_quote_bm25(spec.document_type)}")

    return " AND ".join(clauses) if clauses else None


def build_where(spec: QuerySpec, alias: str = "mp") -> tuple[str, list]:
    """Render ``spec`` as a SQL WHERE clause and positional parameters.

    Indexed filters are folded into one ``paradedb.parse`` query so the BM25
    scan can push them down. ``modified_after`` stays a SQL predicate because
    ``modified`` is not part of the BM25 index. ``alias`` prefixes every column
    so callers can join or subquery the page table; pass ``alias=""`` for an
    unqualified single-table query.
    """
    where: list[str] = []
    params: list = []

    query = _bm25_query(spec)
    if query is not None:
        where.append(f"{_column(alias, 'id')} @@@ paradedb.parse(%s)")
        params.append(query)
    else:
        if spec.municipality_ids:
            where.append(f"{_column(alias, 'municipality_id')} = ANY(%s)")
            params.append(list(spec.municipality_ids))

        if spec.states:
            where.append(f"{_column(alias, 'state')} = ANY(%s)")
            params.append(list(spec.states))

        if spec.date_from:
            where.append(f"{_column(alias, 'meeting_date')} >= %s")
            params.append(spec.date_from)

        if spec.date_to:
            where.append(f"{_column(alias, 'meeting_date')} <= %s")
            params.append(spec.date_to)

        if spec.document_type and spec.document_type != "all":
            where.append(f"{_column(alias, 'document_type')} = %s")
            params.append(spec.document_type)

    if spec.modified_after:
        where.append(f"{_column(alias, 'modified')} >= %s")
        params.append(spec.modified_after)

    if not where:
        # No query and no filters: a plain recency listing, never a BM25 scan
        # over the whole corpus.
        where.append("TRUE")

    return " AND ".join(where), params
