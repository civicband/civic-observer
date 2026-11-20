"""Helpers for ranking :class:`~watch.models.Place` candidates.

A county often shares a normalized name with a city: ``Los Angeles``,
``San Diego``, ``La Crosse``, and ``San Mateo`` all exist as both a county and
a city. Gazetteer aliases deliberately strip the ``County`` suffix, so a lookup
key can match both rows. Settlement candidates must win before any population
comparison: a county's population is usually larger than the eponymous city's,
and leaving the pair to the 10x ambiguity rule silently turns common
subreddits into ``ambiguous`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from watch.models import Place

#: Place types that describe an inhabited settlement rather than an
#: administrative area such as a county or school district.
SETTLEMENT_TYPES: frozenset[str] = frozenset(
    {
        Place.PlaceType.CITY,
        Place.PlaceType.TOWN,
        Place.PlaceType.VILLAGE,
        Place.PlaceType.CDP,
        Place.PlaceType.CANADIAN_CITY,
    }
)


def prefer_settlement_types(places: Iterable[Place]) -> list[Place]:
    """Return settlement candidates when any exist, otherwise every candidate.

    The input is materialized once so the helper accepts generators and
    querysets alike. An empty input returns an empty list.
    """
    candidates = list(places)
    settlements = [
        place for place in candidates if place.place_type in SETTLEMENT_TYPES
    ]
    return settlements or candidates
