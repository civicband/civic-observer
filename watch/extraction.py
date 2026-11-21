"""Deterministic place extraction from thread text.

This module owns the body-pattern rules and the alias-based phrase resolver.
Every place must already exist as a :class:`~watch.models.Place` row; nothing
here creates a place from free text. Body-pattern matches are high-confidence
subjects because phrases such as "Chico City Council" name a governing body.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from watch.models import Place, PlaceAlias, PlaceMention
from watch.normalize import normalize_key
from watch.place_utils import SETTLEMENT_TYPES, prefer_settlement_types

#: Place names that are also ordinary English words. A lower-case use of one
#: is prose, not a reference to the place, so resolution requires a
#: capitalized phrase.
COMMON_WORD_NAMES: frozenset[str] = frozenset(
    {
        "athens",
        "bell",
        "clinton",
        "concord",
        "hope",
        "independence",
        "jackson",
        "lebanon",
        "liberty",
        "madison",
        "marion",
        "mobile",
        "riverside",
        "salem",
        "union",
        "washington",
    }
)

#: Local-government types that can have a city or town council.
MUNICIPAL_TYPES: frozenset[str] = frozenset(
    {
        Place.PlaceType.CITY,
        Place.PlaceType.TOWN,
        Place.PlaceType.VILLAGE,
    }
)

#: Types that can operate a planning commission or similar body. CDPs are
#: included because their extracted place rolls up to the containing county.
GOVERNMENT_TYPES: frozenset[str] = (
    MUNICIPAL_TYPES | SETTLEMENT_TYPES | {Place.PlaceType.COUNTY}
)

#: A bounded place phrase of one to five capitalized words. Patterns compile
#: with ``re.IGNORECASE`` so the suffix ("city council") matches any case; the
#: scoped ``(?-i:...)`` keeps the captured phrase genuinely capitalized so the
#: pattern cannot swallow the lower-case words that follow the place.
PLACE = r"(?P<place>(?-i:[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,4}))"

_BODY_PATTERN_METHOD = PlaceMention.Method.BODY_PATTERN

#: ``(pattern, required_place_type, method)`` rules, ordered from most to
#: least specific so "Riverside County Board of Supervisors" is not also read
#: as a bare board phrase.
BODY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(rf"{PLACE}\s+City\s+Council\b", re.IGNORECASE),
        "municipality",
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(
            rf"{PLACE}\s+County\s+Board\s+of\s+Supervisors\b",
            re.IGNORECASE,
        ),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"{PLACE}\s+Board\s+of\s+Supervisors\b", re.IGNORECASE),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"{PLACE}\s+Planning\s+Commission\b", re.IGNORECASE),
        "government",
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bTown\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.TOWN,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bCity\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.CITY,
        _BODY_PATTERN_METHOD,
    ),
    (
        re.compile(rf"\bCounty\s+of\s+{PLACE}\b", re.IGNORECASE),
        Place.PlaceType.COUNTY,
        _BODY_PATTERN_METHOD,
    ),
]

#: The runner-up must be at least this many times smaller before the most
#: populous candidate wins an otherwise ambiguous phrase.
AMBIGUITY_RATIO = 10


@dataclass(frozen=True)
class ExtractedPlace:
    """One place identified in text, with the evidence that found it."""

    place: Place
    matched_text: str
    method: str
    confidence: str
    is_subject: bool


def extract_body_patterns(
    text: str, *, state: str | None = None
) -> list[ExtractedPlace]:
    """Return places named by governing-body phrases in ``text``.

    Each pattern loads its candidate aliases in one query, however many phrases
    it matches. Duplicate places are collapsed so a county board sentence does
    not produce both the specific and generic board rules.
    """
    results: list[ExtractedPlace] = []
    seen: set[tuple[object, bool]] = set()

    for pattern, required_place_type, method in BODY_PATTERNS:
        phrases = [
            phrase
            for match in pattern.finditer(text)
            if (phrase := match.group("place")) and phrase[:1].isupper()
        ]
        if not phrases:
            continue
        aliases = _load_aliases(phrases)

        for phrase in phrases:
            place, matched_text = _resolve_variants(
                phrase,
                aliases=aliases,
                state=state,
                required_place_type=required_place_type,
            )
            if place is None:
                continue

            key = (place.pk, True)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                ExtractedPlace(
                    place=place,
                    matched_text=matched_text,
                    method=method,
                    confidence=PlaceMention.Confidence.HIGH,
                    is_subject=True,
                )
            )

    return results


def resolve_place_phrase(
    phrase: str,
    *,
    state: str | None = None,
    required_place_type: str | None = None,
) -> Place | None:
    """Resolve a text phrase to an existing place, or return ``None``.

    Aliases are matched by their normalized key. A matching ``state`` is a
    strong prior: when any candidate is in that state, others are discarded.
    Remaining candidates are ranked by population and only resolve when the
    leader is at least :data:`AMBIGUITY_RATIO` times the runner-up. A CDP with a
    known county rolls up to that county because a CDP has no government of its
    own.
    """
    key = normalize_key(phrase)
    if not key:
        return None
    aliases = PlaceAlias.objects.filter(key=key).select_related(
        "place", "place__county"
    )
    return _resolve_candidates(
        phrase,
        (alias.place for alias in aliases),
        state=state,
        required_place_type=required_place_type,
    )


def _load_aliases(phrases: list[str]) -> dict[str, list[Place]]:
    """Return alias places keyed by normalized phrase for one pattern's hits."""
    keys = {
        normalize_key(variant)
        for phrase in phrases
        for variant in _phrase_variants(phrase)
    }
    keys.discard("")
    if not keys:
        return {}

    aliases = PlaceAlias.objects.filter(key__in=keys).select_related(
        "place", "place__county"
    )
    by_key: dict[str, list[Place]] = {}
    for alias in aliases:
        by_key.setdefault(alias.key, []).append(alias.place)
    return by_key


def _resolve_variants(
    phrase: str,
    *,
    aliases: dict[str, list[Place]],
    state: str | None,
    required_place_type: str,
) -> tuple[Place | None, str]:
    """Resolve ``phrase``, retrying without a leading article if needed."""
    for variant in _phrase_variants(phrase):
        place = _resolve_candidates(
            variant,
            aliases.get(normalize_key(variant), []),
            state=state,
            required_place_type=required_place_type,
        )
        if place is not None:
            return place, variant
    return None, phrase


def _resolve_candidates(
    phrase: str,
    candidates: Iterable[Place],
    *,
    state: str | None,
    required_place_type: str | None,
) -> Place | None:
    """Resolve one phrase against already-loaded alias candidates."""
    key = normalize_key(phrase)
    if not key:
        return None
    if key in COMMON_WORD_NAMES and not phrase[:1].isupper():
        return None

    places = _unique_places(candidates)
    places = _filter_place_type(places, required_place_type)
    if not places:
        return None

    places = _apply_state_prior(places, state)
    if required_place_type != Place.PlaceType.COUNTY:
        places = prefer_settlement_types(places)
    place = _choose_place(places)
    if place is None:
        return None
    return _roll_up(place, required_place_type)


def _phrase_variants(phrase: str) -> list[str]:
    """Return every trailing sub-phrase, longest first.

    Greedy patterns can capture a leading preposition or article, so "In
    Beverly Hills" must also try "Beverly Hills" and "Hills".
    """
    words = phrase.split()
    return [" ".join(words[index:]) for index in range(len(words))]


def _unique_places(places: Iterable[Place]) -> list[Place]:
    """Return places de-duplicated by primary key, preserving input order."""
    unique: dict[object, Place] = {}
    for place in places:
        unique[place.pk] = place
    return list(unique.values())


def _filter_place_type(
    places: list[Place], required_place_type: str | None
) -> list[Place]:
    """Return only places allowed by ``required_place_type``."""
    if required_place_type is None:
        return places
    if required_place_type == "government":
        return [
            place
            for place in places
            if place.is_government or place.place_type in GOVERNMENT_TYPES
        ]
    if required_place_type == "municipality":
        allowed: frozenset[str] = MUNICIPAL_TYPES
    else:
        allowed = frozenset({required_place_type})
    return [place for place in places if place.place_type in allowed]


def _apply_state_prior(places: list[Place], state: str | None) -> list[Place]:
    """Narrow to the requested state when any candidate is in it."""
    if not state:
        return places
    code = state.strip().upper()
    in_state = [place for place in places if place.state == code]
    return in_state or places


def _choose_place(places: list[Place]) -> Place | None:
    """Choose a single place by the population ambiguity rule.

    Unknown or zero populations are not evidence of a 10x difference, so a
    comparison only resolves when both candidate populations are known and
    nonzero.
    """
    if len(places) == 1:
        return places[0]

    ranked = sorted(places, key=lambda place: place.population or 0, reverse=True)
    top_population = ranked[0].population or 0
    next_population = ranked[1].population or 0
    if (
        top_population
        and next_population
        and top_population >= AMBIGUITY_RATIO * next_population
    ):
        return ranked[0]
    return None


def _roll_up(place: Place, required_place_type: str | None) -> Place:
    """Roll a CDP up to its county unless a CDP was explicitly requested."""
    if (
        required_place_type != Place.PlaceType.CDP
        and place.place_type == Place.PlaceType.CDP
    ):
        county = place.county
        if county is not None:
            return county
    return place
