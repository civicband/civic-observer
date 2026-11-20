"""Link civic.band ``Muni`` rows to gazetteer :class:`~watch.models.Place` rows.

A municipality is matched conservatively:

1. The exact ``normalize_key(name + state)`` alias is tried first. The
   gazetteer loader writes a ``name_state`` alias for every place, so this
   path resolves the state ambiguity for real data. Two aliases can share a
   key when a county's suffix-stripped name matches a city; city/town/village/
   CDP candidates then win over the county before coordinates are considered.
2. Failing that, places whose name exactly matches the muni name **and whose
   state matches the muni state** are used. A lone match links; several
   matches are resolved by coordinates closer than
   :data:`MAX_TIE_BREAK_DEGREES`; otherwise the muni is reported as ambiguous.
   Equal distances within :data:`TIE_EPSILON` are ambiguous, not a
   lexicographic coin flip.
3. Places already owned by another muni are never reassigned, and places
   already ``confirmed`` by an operator are never overwritten. A muni that
   already has any linked place is left alone and counted as ``already``.

``municipalities.Muni`` is treated as read-only.
"""

from __future__ import annotations

import math
from typing import cast

from django.utils import timezone

from municipalities.models import Muni
from watch.models import Place
from watch.normalize import normalize_key

#: Largest latitude/longitude distance, in degrees, allowed to break a tie.
MAX_TIE_BREAK_DEGREES = 0.25

#: Distances closer than this are treated as an exact tie and stay ambiguous.
TIE_EPSILON = 1e-9

STAT_KEYS: tuple[str, ...] = ("linked", "ambiguous", "no_match", "already")

#: Place types that describe an incorporated municipality. They win over a
#: county or school district sharing the same name+state alias key.
_CIVIC_TYPES = frozenset(
    {
        Place.PlaceType.CITY,
        Place.PlaceType.TOWN,
        Place.PlaceType.VILLAGE,
        Place.PlaceType.CDP,
    }
)

_STATUS_AUTO = "auto"
_STATUS_NO_MATCH = "no_match"
_STATUS_AMBIGUOUS = "ambiguous"


def match_muni(muni: Muni) -> tuple[Place | None, str]:
    """Return the place for ``muni`` and a link status.

    The status is ``auto`` when a single place wins, ``no_match`` when nothing
    shares the muni's name and state, and ``ambiguous`` when several places
    remain after the type preference and coordinate tie-break. Candidates
    owned by another muni also make the muni ambiguous: the existing link is
    never reassigned.
    """
    candidates = _alias_matches(muni) or _name_matches(muni)
    if not candidates:
        return None, _STATUS_NO_MATCH

    available = [place for place in candidates if place.muni_id is None]
    if not available:
        # Every candidate is already owned by another muni.
        return None, _STATUS_AMBIGUOUS

    available = _prefer_civic_types(available)
    if len(available) == 1:
        return available[0], _STATUS_AUTO

    closest = _closest_place(muni, available)
    if closest is None:
        return None, _STATUS_AMBIGUOUS
    return closest, _STATUS_AUTO


def link_munis(*, dry_run: bool = False, limit: int | None = None) -> dict[str, int]:
    """Link munis to places and return ``linked``/``ambiguous``/``no_match``/``already``.

    ``dry_run`` computes the same counts without saving. ``limit`` caps the
    number of munis considered, in ``name``/``subdomain`` order.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")

    stats = dict.fromkeys(STAT_KEYS, 0)
    linked_muni_ids = set(
        Place.objects.filter(muni__isnull=False).values_list("muni_id", flat=True)
    )

    munis = Muni.objects.order_by("name", "subdomain")
    if limit is not None:
        munis = munis[:limit]

    for muni in munis.iterator():
        if muni.pk in linked_muni_ids:
            stats["already"] += 1
            continue

        place, status = match_muni(muni)
        if place is None:
            stats[status] += 1
            continue

        if not dry_run:
            place.muni = muni
            place.muni_link_status = Place.MuniLinkStatus.AUTO
            place.muni_reviewed_at = timezone.now()
            place.save(
                update_fields=[
                    "muni",
                    "muni_link_status",
                    "muni_reviewed_at",
                    "modified",
                ]
            )
        stats["linked"] += 1

    return stats


def _alias_matches(muni: Muni) -> list[Place]:
    """Return unconfirmed places whose key is the muni's name+state key."""
    key = normalize_key(f"{muni.name}{muni.state}")
    if not key:
        return []
    places = (
        Place.objects.filter(aliases__key=key)
        .exclude(muni_link_status=Place.MuniLinkStatus.CONFIRMED)
        .distinct()
        .order_by("canonical_key")
    )
    return list(places)


def _name_matches(muni: Muni) -> list[Place]:
    """Return unconfirmed same-state places whose name matches the muni name."""
    name = " ".join(muni.name.split())
    if not name:
        return []
    places = (
        Place.objects.filter(name__iexact=name, state__iexact=muni.state)
        .exclude(muni_link_status=Place.MuniLinkStatus.CONFIRMED)
        .order_by("canonical_key")
    )
    return list(places)


def _prefer_civic_types(candidates: list[Place]) -> list[Place]:
    """Narrow candidates to cities/towns/villages/CDPs when any exist."""
    civic = [place for place in candidates if place.place_type in _CIVIC_TYPES]
    return civic or candidates


def _closest_place(muni: Muni, candidates: list[Place]) -> Place | None:
    """Return the nearest candidate within the tie-break distance, if any."""
    muni_latitude = muni.latitude
    muni_longitude = muni.longitude
    if muni_latitude is None or muni_longitude is None:
        return None

    located = [
        place
        for place in candidates
        if place.latitude is not None and place.longitude is not None
    ]
    if not located:
        return None

    def distance(place: Place) -> float:
        return math.dist(
            (muni_latitude, muni_longitude),
            (cast(float, place.latitude), cast(float, place.longitude)),
        )

    ranked = sorted(located, key=distance)
    closest = ranked[0]
    if distance(closest) >= MAX_TIE_BREAK_DEGREES:
        return None
    if len(ranked) > 1 and math.isclose(
        distance(closest), distance(ranked[1]), rel_tol=0.0, abs_tol=TIE_EPSILON
    ):
        return None
    return closest
