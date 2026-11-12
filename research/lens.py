"""URL-encoded filter state for browsing a materialized research set."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

DEFAULT_DISPOSITIONS = ("u", "r")
ORDERS = ("rank", "date_desc", "date_asc")

_TRUTHY = {"1", "true", "yes", "on"}


def _raw_values(qd, key: str) -> list:
    if hasattr(qd, "getlist"):
        return list(qd.getlist(key))

    raw = qd.get(key, [])
    if isinstance(raw, list | tuple):
        return list(raw)
    if raw in (None, ""):
        return []
    return [raw]


def _split_values(qd, key: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw in _raw_values(qd, key):
        for part in str(raw).split(","):
            part = part.strip()
            if part and part not in values:
                values.append(part)
    return tuple(values)


def _uuid_values(qd, key: str) -> tuple[uuid.UUID, ...]:
    values: list[uuid.UUID] = []
    for value in _split_values(qd, key):
        try:
            values.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return tuple(values)


def _scalar(qd, key: str, default: str = "") -> str:
    for raw in _raw_values(qd, key):
        value = str(raw).strip()
        if value:
            return value
    return default


def _date_value(qd, key: str) -> date | None:
    value = _scalar(qd, key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Lens:
    text: str = ""
    dispositions: tuple[str, ...] = DEFAULT_DISPOSITIONS
    codes_all: tuple[uuid.UUID, ...] = ()
    codes_any: tuple[uuid.UUID, ...] = ()
    codes_none: tuple[uuid.UUID, ...] = ()
    uncoded: bool = False
    municipality_ids: tuple[uuid.UUID, ...] = ()
    states: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = "all"
    added_via: tuple[str, ...] = ()
    order: str = "rank"

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text or "")
        object.__setattr__(self, "dispositions", tuple(self.dispositions))
        object.__setattr__(self, "codes_all", tuple(self.codes_all))
        object.__setattr__(self, "codes_any", tuple(self.codes_any))
        object.__setattr__(self, "codes_none", tuple(self.codes_none))
        object.__setattr__(self, "municipality_ids", tuple(self.municipality_ids))
        object.__setattr__(self, "states", tuple(self.states))
        object.__setattr__(self, "document_type", self.document_type or "all")
        object.__setattr__(self, "added_via", tuple(self.added_via))
        object.__setattr__(
            self, "order", self.order if self.order in ORDERS else "rank"
        )

    @classmethod
    def from_querydict(cls, qd) -> Lens:
        if "disposition" in qd:
            dispositions = _split_values(qd, "disposition")
        else:
            dispositions = DEFAULT_DISPOSITIONS
        order = _scalar(qd, "order", "rank")
        return cls(
            text=_scalar(qd, "q"),
            dispositions=dispositions,
            codes_all=_uuid_values(qd, "code_all"),
            codes_any=_uuid_values(qd, "code_any"),
            codes_none=_uuid_values(qd, "code_none"),
            uncoded=_scalar(qd, "uncoded").lower() in _TRUTHY,
            municipality_ids=_uuid_values(qd, "municipality"),
            states=_split_values(qd, "state"),
            date_from=_date_value(qd, "date_from"),
            date_to=_date_value(qd, "date_to"),
            document_type=_scalar(qd, "document_type", "all"),
            added_via=_split_values(qd, "added_via"),
            order=order,
        )

    def canonical(self) -> dict:
        return {
            "text": self.text,
            "dispositions": list(self.dispositions),
            "codes_all": [str(value) for value in self.codes_all],
            "codes_any": [str(value) for value in self.codes_any],
            "codes_none": [str(value) for value in self.codes_none],
            "uncoded": self.uncoded,
            "municipality_ids": [str(value) for value in self.municipality_ids],
            "states": list(self.states),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "document_type": self.document_type,
            "added_via": list(self.added_via),
            "order": self.order,
        }

    @classmethod
    def from_canonical(cls, data: dict) -> Lens:
        return cls(
            text=data.get("text", ""),
            dispositions=tuple(data.get("dispositions", DEFAULT_DISPOSITIONS)),
            codes_all=tuple(uuid.UUID(value) for value in data.get("codes_all", [])),
            codes_any=tuple(uuid.UUID(value) for value in data.get("codes_any", [])),
            codes_none=tuple(uuid.UUID(value) for value in data.get("codes_none", [])),
            uncoded=bool(data.get("uncoded", False)),
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
            added_via=tuple(data.get("added_via", [])),
            order=data.get("order", "rank"),
        )

    def to_querystring(self, *, include_dispositions: bool = False) -> str:
        pairs: list[tuple[str, str]] = []
        if self.text:
            pairs.append(("q", self.text))
        if include_dispositions or self.dispositions != DEFAULT_DISPOSITIONS:
            pairs.append(("disposition", ",".join(self.dispositions)))
        if self.states:
            pairs.append(("state", ",".join(self.states)))
        if self.municipality_ids:
            pairs.append(
                (
                    "municipality",
                    ",".join(str(value) for value in self.municipality_ids),
                )
            )
        if self.codes_any:
            pairs.append(("code_any", ",".join(str(value) for value in self.codes_any)))
        if self.codes_all:
            pairs.append(("code_all", ",".join(str(value) for value in self.codes_all)))
        if self.codes_none:
            pairs.append(
                ("code_none", ",".join(str(value) for value in self.codes_none))
            )
        if self.uncoded:
            pairs.append(("uncoded", "1"))
        if self.date_from:
            pairs.append(("date_from", self.date_from.isoformat()))
        if self.date_to:
            pairs.append(("date_to", self.date_to.isoformat()))
        if self.document_type != "all":
            pairs.append(("document_type", self.document_type))
        if self.added_via:
            pairs.append(("added_via", ",".join(self.added_via)))
        if self.order != "rank":
            pairs.append(("order", self.order))
        return urlencode(pairs)
