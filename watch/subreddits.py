"""Seed manual subreddit labels and hand-written place aliases.

The CSV files in ``watch/data`` are version-controlled operator data: the
subreddit labels capture the hand review of the 63-label set, and the alias
file records nicknames that the generated Gazetteer aliases cannot infer.

Seeding is idempotent. Re-running it refreshes the classifier fields from the
CSV but never clears an operator mute or replaces an operator note with a
blank value.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from watch.models import Place, PlaceAlias, Subreddit
from watch.normalize import normalize_key

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

_TRUE_VALUES = {"1", "true", "yes", "y", "x"}


def infer_country(note: str) -> str | None:
    """Return the ISO country code named in a manual note, if any."""
    if not note:
        return None
    for pattern, code in _COUNTRY_PATTERNS:
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


def seed_subreddits(csv_path: Path) -> dict[str, int]:
    """Upsert manual subreddit labels from ``csv_path``.

    Returns counts of created and updated rows plus the number of rows that
    named a place or county that could not be resolved unambiguously.
    """
    stats = {"created": 0, "updated": 0, "unresolved": 0}
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

            defaults = {
                "kind": _cell(row, "kind"),
                "place": place,
                "state": state,
                "country": infer_country(note),
                "confidence": _cell(row, "confidence"),
                "source": Subreddit.Source.MANUAL,
                "reviewed_at": now,
                "muted": muted,
                "note": note,
            }

            existing = Subreddit.objects.filter(name=name).first()
            if existing is not None:
                # Operator state wins over blank CSV values.
                defaults["muted"] = existing.muted or muted
                defaults["note"] = note or existing.note

            _, created = Subreddit.objects.update_or_create(
                name=name, defaults=defaults
            )
            stats["created" if created else "updated"] += 1

    return stats


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
