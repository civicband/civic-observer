"""Shared fixtures for watch extraction tests."""

from __future__ import annotations

import pytest

from watch.gazetteer import refresh_aliases
from watch.models import Place


def _place(
    canonical_key: str,
    name: str,
    place_type: str,
    state: str,
    *,
    population: int | None = None,
    county: Place | None = None,
) -> Place:
    """Create a place and the aliases the gazetteer loader would generate."""
    place = Place.objects.create(
        canonical_key=canonical_key,
        name=name,
        legal_name=name,
        place_type=place_type,
        state=state,
        population=population,
        county=county,
    )
    refresh_aliases(place)
    return place


@pytest.fixture
def extraction_places(db: None) -> dict[str, Place]:
    """Create the places named by the milestone 5 extraction examples."""
    contra_costa = _place(
        "us:county:06013",
        "Contra Costa County",
        Place.PlaceType.COUNTY,
        "CA",
        population=1165927,
    )
    riverside_county = _place(
        "us:county:06065",
        "Riverside County",
        Place.PlaceType.COUNTY,
        "CA",
        population=2418185,
    )
    san_diego_county = _place(
        "us:county:06073",
        "San Diego County",
        Place.PlaceType.COUNTY,
        "CA",
        population=3298634,
    )
    places = {
        "chico": _place(
            "us:place:0613010",
            "Chico",
            Place.PlaceType.CITY,
            "CA",
            population=101724,
        ),
        "beverly_hills": _place(
            "us:place:0606300",
            "Beverly Hills",
            Place.PlaceType.CITY,
            "CA",
            population=32300,
        ),
        "riverside_county": riverside_county,
        "riverside": _place(
            "us:place:0662000",
            "Riverside",
            Place.PlaceType.CITY,
            "CA",
            population=314998,
            county=riverside_county,
        ),
        "contra_costa_county": contra_costa,
        "san_diego_county": san_diego_county,
        "san_diego": _place(
            "us:place:0666000",
            "San Diego",
            Place.PlaceType.CITY,
            "CA",
            population=1386932,
            county=san_diego_county,
        ),
        "north_richmond": _place(
            "us:place:0651880",
            "North Richmond",
            Place.PlaceType.CDP,
            "CA",
            population=4112,
            county=contra_costa,
        ),
        "mobile": _place(
            "us:place:0150000",
            "Mobile",
            Place.PlaceType.CITY,
            "AL",
            population=187041,
        ),
        "springfield_il": _place(
            "us:place:1772000",
            "Springfield",
            Place.PlaceType.CITY,
            "IL",
            population=114394,
        ),
        "springfield_mo": _place(
            "us:place:2964000",
            "Springfield",
            Place.PlaceType.CITY,
            "MO",
            population=169176,
        ),
    }
    return places
