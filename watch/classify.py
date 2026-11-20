"""Layered classifier for watched subreddits.

The classifier is deliberately conservative: operator labels win, Wikidata
links win next, and the gazetteer only fills the remaining rows. When the
gazetteer cannot pick a single place, the row is marked ``ambiguous`` instead
of guessing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from django.utils import timezone

from watch.models import NonUsCity, NonUsCityAlias, Place, PlaceAlias, Subreddit
from watch.normalize import STATE_NAMES, normalize_key

#: Prefixes and suffixes that commonly wrap a place name in a subreddit name.
AFFIXES: tuple[str, ...] = (
    "development",
    "daily",
    "news",
    "talk",
    "landlords",
    "ask",
    "r4r",
    "jobs",
)

#: Region subreddit keys mapped to ``(state, display name)``.
REGIONS: dict[str, tuple[str | None, str]] = {
    "bayarea": ("CA", "Bay Area"),
    "socal": ("CA", "Southern California"),
    "pnw": (None, "Pacific Northwest"),
    "pnwdaily": (None, "Pacific Northwest"),
    "dmv": (None, "DC/Maryland/Virginia"),
}

#: The most populous candidate must be this many times the runner-up.
AMBIGUITY_RATIO = 10

#: Normalized state abbreviation and full name keys. Built once at import so
#: classification never queries the database for a state name.
STATE_KEYS: tuple[tuple[str, str], ...] = tuple(
    (normalize_key(value), code)
    for code, name in STATE_NAMES.items()
    for value in (code, name)
)
_STATE_LOOKUP: dict[str, str] = dict(STATE_KEYS)


@dataclass(frozen=True)
class Classification:
    """One proposed label for a subreddit."""

    kind: str
    place: Place | None
    state: str
    country: str | None
    confidence: str
    source: str
    note: str = ""


def classify_subreddit(name: str, *, use_manual: bool = True) -> Classification:
    """Return the first classification layer that matches ``name``."""
    raw_name = name.strip()
    row = Subreddit.objects.filter(name__iexact=raw_name).order_by("name").first()

    if row is not None:
        if use_manual and row.source == Subreddit.Source.MANUAL:
            return _from_row(row)
        if row.source == Subreddit.Source.WIKIDATA:
            return _from_row(row)

    key = normalize_key(raw_name)
    if not key:
        return _general()

    place, ambiguous = _alias_place(key)
    if place is not None:
        return _place_classification(place, Subreddit.Confidence.MEDIUM)
    if ambiguous:
        return _ambiguous(Subreddit.Confidence.LOW)

    for stripped in _stripped_keys(key):
        place, ambiguous = _alias_place(stripped)
        if place is not None:
            return _place_classification(place, Subreddit.Confidence.LOW)
        if ambiguous:
            return _ambiguous(Subreddit.Confidence.LOW)

        match = _state_or_region(stripped, Subreddit.Confidence.LOW)
        if match is not None:
            return match

        match = _non_us_city(stripped, Subreddit.Confidence.LOW)
        if match is not None:
            return match

    match = _state_or_region(key, Subreddit.Confidence.HIGH)
    if match is not None:
        return match

    match = _non_us_city(key, Subreddit.Confidence.MEDIUM)
    if match is not None:
        return match

    return _general()


def apply_classification(subreddit: Subreddit, classification: Classification) -> bool:
    """Write ``classification`` onto ``subreddit`` and report whether it changed.

    Operator state is preserved: ``muted`` is never touched, and a non-blank
    existing note is never replaced.
    """
    note = (subreddit.note or "") or (classification.note or "")
    place_id = classification.place.pk if classification.place is not None else None
    country = str(classification.country) if classification.country else None
    state = classification.state or ""

    changed = (
        subreddit.kind != classification.kind
        or subreddit.place_id != place_id
        or (subreddit.state or "") != state
        or (str(subreddit.country) if subreddit.country else "") != (country or "")
        or subreddit.confidence != classification.confidence
        or subreddit.source != classification.source
        or (subreddit.note or "") != note
        or subreddit.reviewed_at is None
    )
    if not changed:
        return False

    subreddit.kind = classification.kind
    subreddit.place = classification.place
    subreddit.state = state
    subreddit.country = country
    subreddit.confidence = classification.confidence
    subreddit.source = classification.source
    subreddit.note = note
    subreddit.reviewed_at = timezone.now()
    subreddit.save(
        update_fields=[
            "kind",
            "place",
            "state",
            "country",
            "confidence",
            "source",
            "note",
            "reviewed_at",
        ]
    )
    return True


def classify_pending_subreddits(
    *, reclassify: bool = False, limit: int | None = None
) -> dict[str, int]:
    """Classify rows awaiting review, or every non-manual row on reclassify.

    Returns counts of rows checked, changed, and left unchanged.
    """
    queryset = Subreddit.objects.all()
    if reclassify:
        queryset = queryset.exclude(source=Subreddit.Source.MANUAL)
    else:
        queryset = queryset.filter(reviewed_at__isnull=True)
    queryset = queryset.order_by("name")
    if limit is not None:
        queryset = queryset[:limit]

    stats = {"checked": 0, "changed": 0, "unchanged": 0}
    for subreddit in queryset:
        classification = classify_subreddit(subreddit.name, use_manual=True)
        stats["checked"] += 1
        if apply_classification(subreddit, classification):
            stats["changed"] += 1
        else:
            stats["unchanged"] += 1
    return stats


def _from_row(row: Subreddit) -> Classification:
    """Return the review row's own labels."""
    return Classification(
        kind=row.kind,
        place=row.place,
        state=row.state or "",
        country=str(row.country) if row.country else None,
        confidence=row.confidence,
        source=row.source,
        note=row.note or "",
    )


def _place_classification(place: Place, confidence: str) -> Classification:
    """Return a ``place`` classification for ``place``."""
    return Classification(
        kind=Subreddit.Kind.PLACE,
        place=place,
        state=place.state or "",
        country=str(place.country) or None,
        confidence=confidence,
        source=Subreddit.Source.GAZETTEER,
    )


def _ambiguous(confidence: str) -> Classification:
    return Classification(
        kind=Subreddit.Kind.AMBIGUOUS,
        place=None,
        state="",
        country=None,
        confidence=confidence,
        source=Subreddit.Source.GAZETTEER,
    )


def _general() -> Classification:
    return Classification(
        kind=Subreddit.Kind.GENERAL,
        place=None,
        state="",
        country=None,
        confidence=Subreddit.Confidence.LOW,
        source=Subreddit.Source.GAZETTEER,
    )


def _stripped_keys(key: str) -> tuple[str, ...]:
    """Return keys with one known affix removed from either end."""
    candidates: list[str] = []
    for affix in AFFIXES:
        if key.startswith(affix) and len(key) > len(affix):
            stripped = key[len(affix) :]
        elif key.endswith(affix) and len(key) > len(affix):
            stripped = key[: -len(affix)]
        else:
            continue
        if stripped not in candidates:
            candidates.append(stripped)
    return tuple(candidates)


def _alias_place(key: str) -> tuple[Place | None, bool]:
    """Return the alias candidate for ``key`` and whether it is ambiguous."""
    aliases = PlaceAlias.objects.filter(key=key).select_related("place")
    return _choose_place(alias.place for alias in aliases)


def _choose_place(candidates: Iterable[Place]) -> tuple[Place | None, bool]:
    """Pick a place by population, or report ambiguity.

    A single distinct place always wins. Several places resolve only when the
    most populous has at least :data:`AMBIGUITY_RATIO` times the runner-up.
    """
    unique: dict[object, Place] = {}
    for place in candidates:
        unique[place.pk] = place

    places = list(unique.values())
    if not places:
        return None, False
    if len(places) == 1:
        return places[0], False

    ranked = sorted(places, key=lambda place: place.population or 0, reverse=True)
    top_population = ranked[0].population or 0
    next_population = ranked[1].population or 0
    if top_population and (
        next_population == 0 or top_population >= AMBIGUITY_RATIO * next_population
    ):
        return ranked[0], False
    return None, True


def _state_or_region(key: str, confidence: str) -> Classification | None:
    """Return a state or region classification for an exact key."""
    code = _STATE_LOOKUP.get(key)
    if code is not None:
        return Classification(
            kind=Subreddit.Kind.STATE,
            place=None,
            state=code,
            country=None,
            confidence=confidence,
            source=Subreddit.Source.GAZETTEER,
        )

    region = REGIONS.get(key)
    if region is not None:
        state, note = region
        return Classification(
            kind=Subreddit.Kind.REGION,
            place=None,
            state=state or "",
            country=None,
            confidence=confidence,
            source=Subreddit.Source.GAZETTEER,
            note=note,
        )
    return None


def _non_us_city(key: str, confidence: str) -> Classification | None:
    """Return a classification for a non-US city alias.

    Canadian cities that also exist as gazetteer places resolve to the place;
    other cities stay ``non_us`` with their country code.
    """
    alias = (
        NonUsCityAlias.objects.filter(key=key)
        .select_related("city")
        .order_by("-city__population", "city__name")
        .first()
    )
    if alias is None:
        return None

    city = alias.city
    if str(city.country) == "CA":
        place, ambiguous = _canadian_place(key, city)
        if place is not None:
            return _place_classification(place, confidence)
        if ambiguous:
            return _ambiguous(confidence)

    return Classification(
        kind=Subreddit.Kind.NON_US,
        place=None,
        state="",
        country=str(city.country) or None,
        confidence=confidence,
        source=Subreddit.Source.GAZETTEER,
    )


def _canadian_place(key: str, city: NonUsCity) -> tuple[Place | None, bool]:
    """Return the Canadian gazetteer place for a non-US city alias."""
    aliases = PlaceAlias.objects.filter(key=key, place__country="CA").select_related(
        "place"
    )
    candidates = {alias.place.pk: alias.place for alias in aliases}
    if not candidates:
        candidates = {
            place.pk: place
            for place in Place.objects.filter(country="CA", name__iexact=city.name)
        }
    return _choose_place(candidates.values())
