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
    def from_search(cls, search, *, modified_after: datetime | None = None):
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
