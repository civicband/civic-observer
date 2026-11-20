"""Seed manual subreddit labels and hand-written place aliases.

The CSV files in ``watch/data`` are version-controlled operator data: the
subreddit labels capture the hand review of the 63-label set, and the alias
file records nicknames that the generated Gazetteer aliases cannot infer.

Seeding is idempotent and does not destroy operator edits. Existing rows keep
their ``note``, ``place``, ``kind``, ``country``, and ``reviewed_at`` values;
only new rows take those values from the CSV. ``muted`` is always preserved,
and ``--refresh-labels`` forces the CSV values back onto existing rows when an
operator explicitly wants that.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from watch.models import Place, PlaceAlias, Subreddit
from watch.normalize import PROVINCE_NAMES, normalize_key

DATA_DIR = Path(__file__).resolve().parent / "data"
SUBREDDITS_CSV = DATA_DIR / "subreddits.csv"
ALIASES_CSV = DATA_DIR / "aliases.csv"

# Settlement types are valid targets for a subreddit's ``place`` column. A
# county or state with the same name must not shadow the city.
_SETTLEMENT_TYPES: tuple[str, ...] = (
    Place.PlaceType.CITY,
    Place.PlaceType.TOWN,
    Place.PlaceType.VILLAGE,
    Place.PlaceType.CDP,
    Place.PlaceType.CANADIAN_CITY,
)

_COUNTRY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bireland\b", re.IGNORECASE), "IE"),
    (re.compile(r"\bunited kingdom\b", re.IGNORECASE), "GB"),
    (re.compile(r"\buk\b", re.IGNORECASE), "GB"),
    (re.compile(r"\baustralia\b", re.IGNORECASE), "AU"),
    (re.compile(r"\bnew zealand\b", re.IGNORECASE), "NZ"),
    (re.compile(r"\bcanada\b", re.IGNORECASE), "CA"),
)

# Canadian province names and codes. Codes are matched case-sensitively so a
# common word such as "on" is not read as Ontario.
_PROVINCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), "CA")
    for name in PROVINCE_NAMES.values()
) + tuple(
    (re.compile(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])"), "CA")
    for code in PROVINCE_NAMES
)

#: Fields that belong to the operator once a row exists.
_PRESERVED_LABEL_FIELDS: tuple[str, ...] = (
    "kind",
    "place",
    "country",
    "reviewed_at",
    "note",
)

_TRUE_VALUES = {"1", "true", "yes", "y", "x"}


def infer_country(note: str) -> str | None:
    """Return the ISO country code named in a manual note, if any."""
    if not note:
        return None
    for pattern, code in (*_COUNTRY_PATTERNS, *_PROVINCE_PATTERNS):
        if pattern.search(note):
            return code
    return None


def resolve_manual_place(
    name: str | None,
    state: str | None,
    county: str | None,
) -> Place | None:
    """Resolve a manual row to exactly one place, or ``None``.

    Rows with a ``name`` resolve against settlement places in the named state.
    Rows with only a ``county`` resolve against county places, never a city.
    A blank state is allowed only when the normalized name matches one place.
    """
    name = (name or "").strip()
    county = (county or "").strip()
    state_code = (state or "").strip().upper()

    if name:
        matches = Place.objects.filter(
            place_type__in=_SETTLEMENT_TYPES, name__iexact=name
        )
        if state_code:
            matches = matches.filter(state=state_code)
        found = list(matches[:2])
        return found[0] if len(found) == 1 else None

    if county:
        wanted = normalize_key(county)
        if not wanted:
            return None
        matches = Place.objects.filter(place_type=Place.PlaceType.COUNTY)
        if state_code:
            matches = matches.filter(state=state_code)
        found = [
            place
            for place in matches
            if wanted
            in {
                normalize_key(place.name),
                normalize_key(place.legal_name or ""),
            }
        ]
        return found[0] if len(found) == 1 else None

    return None


def seed_subreddits(csv_path: Path, *, refresh_labels: bool = False) -> dict[str, int]:
    """Upsert manual subreddit labels from ``csv_path``.

    New rows take every CSV value. Existing rows keep the operator-owned
    fields (``note``, ``place``, ``kind``, ``country``, and ``reviewed_at``)
    unless ``refresh_labels`` is true. ``muted`` is preserved in every mode.
    State, confidence, and source are refreshed from the CSV because they
    describe the manual label rather than an operator decision.

    Returns counts of created, updated, and unchanged rows plus the number of
    rows that named a place or county that could not be resolved
    unambiguously.
    """
    stats = {"created": 0, "updated": 0, "unchanged": 0, "unresolved": 0}
    now = timezone.now()

    with transaction.atomic():
        for row in _read_rows(csv_path):
            name = _cell(row, "subreddit").lower()
            if not name:
                continue

            place_name = _cell(row, "place")
            county_name = _cell(row, "county")
            state = _cell(row, "state").upper()
            note = _cell(row, "note")
            muted = _truthy(_cell(row, "muted"))

            place = resolve_manual_place(
                place_name or None,
                state or None,
                county_name or None,
            )
            if (place_name or county_name) and place is None:
                stats["unresolved"] += 1

            values: dict[str, object] = {
                "kind": _cell(row, "kind"),
                "place": place,
                "state": state,
                "country": infer_country(note),
                "confidence": _cell(row, "confidence"),
                "source": Subreddit.Source.MANUAL,
                "reviewed_at": now,
                "note": note,
            }

            # Match case-insensitively so an operator's "ChicoCA" row is the
            # row this CSV line updates rather than a duplicate.
            existing = (
                Subreddit.objects.filter(name__iexact=name).order_by("name").first()
            )
            if existing is None:
                Subreddit.objects.create(name=name, muted=muted, **values)
                stats["created"] += 1
                continue

            fields: dict[str, object] = {
                "state": values["state"],
                "confidence": values["confidence"],
                "source": values["source"],
            }
            if refresh_labels:
                for field in _PRESERVED_LABEL_FIELDS:
                    fields[field] = values[field]

            target_muted = existing.muted or muted
            if target_muted != existing.muted:
                fields["muted"] = target_muted

            changed = [
                field
                for field, value in fields.items()
                if _field_differs(existing, field, value)
            ]
            if changed:
                for field in changed:
                    setattr(existing, field, fields[field])
                existing.save(update_fields=[*changed, "modified"])
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

    return stats


def _field_differs(instance: Subreddit, field: str, value: object) -> bool:
    """Return whether assigning ``value`` to ``field`` would change the row."""
    if field == "place":
        current = instance.place_id
        wanted = value.pk if isinstance(value, Place) else None
        return current != wanted
    if field == "country":
        current_country = str(instance.country) if instance.country else ""
        wanted_country = str(value) if value else ""
        return current_country != wanted_country
    return getattr(instance, field) != value


def seed_aliases(csv_path: Path) -> int:
    """Upsert manual aliases from ``csv_path`` and return the number written.

    Columns are ``key,place,state`` plus an optional ``county``. Rows whose
    place cannot be resolved unambiguously are skipped.
    """
    written = 0

    with transaction.atomic():
        for row in _read_rows(csv_path):
            key = normalize_key(_cell(row, "key"))
            if not key:
                continue
            place = resolve_manual_place(
                _cell(row, "place") or None,
                _cell(row, "state") or None,
                _cell(row, "county") or None,
            )
            if place is None:
                continue

            PlaceAlias.objects.update_or_create(
                place=place,
                key=key,
                kind=PlaceAlias.Kind.NICKNAME,
                defaults={"source": PlaceAlias.Source.MANUAL},
            )
            written += 1

    return written


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into stripped string rows."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        for row in reader:
            cells: dict[str, str] = {}
            for key, value in row.items():
                if not isinstance(key, str):
                    continue
                if isinstance(value, list):
                    value = ",".join(str(part) for part in value)
                cells[key.strip()] = (value or "").strip()
            if any(cells.values()):
                rows.append(cells)
        return rows


def _cell(row: dict[str, str], name: str) -> str:
    """Return one stripped cell, tolerating a missing column."""
    return row.get(name, "").strip()


def _truthy(value: str) -> bool:
    """Return whether a CSV boolean cell is affirmative."""
    return value.strip().lower() in _TRUE_VALUES
