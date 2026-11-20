"""Fixture data for the layered subreddit classifier tests.

The fixture mirrors the gazetteer rows that the real loader would create: one
:class:`~watch.models.Place` per city or county named in
``watch/data/subreddits.csv``, generated aliases, manual nicknames, and the
non-US city lookup rows needed by the hand-reviewed labels.
"""

from __future__ import annotations

from watch.gazetteer import refresh_aliases
from watch.models import NonUsCity, NonUsCityAlias, Place, PlaceAlias
from watch.normalize import normalize_key

#: ``(canonical_key, name, state, population)`` for settlement rows.
_CITIES: tuple[tuple[str, str, str, int], ...] = (
    ("us:place:0613010", "Chico", "CA", 101724),
    ("us:place:0666000", "San Diego", "CA", 1386932),
    ("us:place:0644000", "Los Angeles", "CA", 3898747),
    ("us:place:3638077", "Ithaca", "NY", 32108),
    ("us:place:4967000", "Salt Lake City", "UT", 200133),
    ("us:place:3611000", "Buffalo", "NY", 278349),
    ("us:place:0669000", "Santa Ana", "CA", 310227),
    ("us:place:4159000", "Portland", "OR", 652503),
    ("us:place:0655254", "Palm Springs", "CA", 48445),
    ("us:place:3011380", "Butte", "MT", 34494),
    ("us:place:5540750", "La Crosse", "WI", 52321),
    ("us:place:5363000", "Seattle", "WA", 737015),
    ("us:place:0659440", "Rancho Cordova", "CA", 79332),
    ("us:place:3719000", "Durham", "NC", 283506),
    ("us:place:4845744", "McKinney", "TX", 195308),
    ("us:place:0827865", "Fountain", "CO", 29802),
    ("us:place:0845250", "Littleton", "CO", 45652),
    ("us:place:4123850", "Eugene", "OR", 176654),
    ("us:place:0668252", "San Mateo", "CA", 105661),
    ("us:place:1938595", "Iowa City", "IA", 74828),
    ("us:place:0620910", "East Palo Alto", "CA", 30099),
    ("us:place:0630000", "Glendale", "CA", 196543),
    ("us:place:0427824", "Glendale", "AZ", 248325),
)

#: ``(canonical_key, name, state)`` for county rows.
_COUNTIES: tuple[tuple[str, str, str], ...] = (
    ("us:county:24015", "Cecil County", "MD"),
    ("us:county:42029", "Chester County", "PA"),
)

#: ``(geoname_id, name, country, admin1, population)`` for non-US cities.
_NON_US_CITIES: tuple[tuple[int, str, str, str, int], ...] = (
    (2643743, "London", "GB", "ENG", 8961989),
    (2964574, "Dublin", "IE", "L", 1173179),
    (2655984, "Belfast", "GB", "NIR", 333871),
    (2193733, "Auckland", "NZ", "AUK", 417910),
    (6173331, "Vancouver", "CA", "BC", 662248),
)

#: Extra manual nickname alias used by ``developmentslc``.
_NICKNAMES: tuple[tuple[str, str, str], ...] = (("slc", "Salt Lake City", "UT"),)


def _make_place(
    canonical_key: str,
    name: str,
    state: str,
    *,
    place_type: str = Place.PlaceType.CITY,
    country: str = "US",
    population: int | None = None,
) -> Place:
    """Create one place with the same generated aliases as the loader."""
    place = Place.objects.create(
        canonical_key=canonical_key,
        geoid=canonical_key.rsplit(":", 1)[-1],
        name=name,
        legal_name=name,
        place_type=place_type,
        state=state,
        country=country,
        population=population,
    )
    refresh_aliases(place)
    return place


def create_classifier_places() -> dict[str, Place | None]:
    """Create the gazetteer rows needed to reproduce the hand labels.

    Returns a mapping of subreddit name to the expected place. ``None`` marks
    rows the classifier may legitimately leave ambiguous or unmatched.
    """
    by_name: dict[tuple[str, str], Place] = {}
    for canonical_key, name, state, population in _CITIES:
        by_name[(name, state)] = _make_place(
            canonical_key, name, state, population=population
        )
    for canonical_key, name, state in _COUNTIES:
        by_name[(name, state)] = _make_place(
            canonical_key,
            name,
            state,
            place_type=Place.PlaceType.COUNTY,
        )

    vancouver = _make_place(
        "ca:place:6173331",
        "Vancouver",
        "BC",
        place_type=Place.PlaceType.CANADIAN_CITY,
        country="CA",
        population=662248,
    )

    for key, name, state in _NICKNAMES:
        PlaceAlias.objects.create(
            place=by_name[(name, state)],
            key=normalize_key(key),
            kind=PlaceAlias.Kind.NICKNAME,
            source=PlaceAlias.Source.MANUAL,
        )

    for geoname_id, name, country, admin1, population in _NON_US_CITIES:
        city = NonUsCity.objects.create(
            geoname_id=geoname_id,
            name=name,
            normalized_name=normalize_key(name),
            country=country,
            admin1=admin1,
            population=population,
        )
        NonUsCityAlias.objects.create(city=city, key=normalize_key(name))

    # An alias the Place deliberately lacks, so the Canadian fallback in the
    # non-US layer is exercised rather than the plain alias layer.
    NonUsCityAlias.objects.create(
        city=NonUsCity.objects.get(name="Vancouver"),
        key="vancity",
    )

    return {
        "sandiego": by_name[("San Diego", "CA")],
        "chicoca": by_name[("Chico", "CA")],
        "losangeles": by_name[("Los Angeles", "CA")],
        "chestercounty": by_name[("Chester County", "PA")],
        "ithaca": by_name[("Ithaca", "NY")],
        "developmentslc": by_name[("Salt Lake City", "UT")],
        "buffalo": by_name[("Buffalo", "NY")],
        "santaana": by_name[("Santa Ana", "CA")],
        "portland": by_name[("Portland", "OR")],
        "palmsprings": by_name[("Palm Springs", "CA")],
        "butte": by_name[("Butte", "MT")],
        "lacrossewi": by_name[("La Crosse", "WI")],
        "seattle": by_name[("Seattle", "WA")],
        "ranchocordova": by_name[("Rancho Cordova", "CA")],
        "mckinney": by_name[("McKinney", "TX")],
        # These hand labels rely on operator notes or nicknames that are not
        # in the generated gazetteer aliases, so the classifier may leave them
        # ``general``; the fixture still records the intended place so a wrong
        # match fails the no-wrong-place assertion.
        "sfv": by_name[("Los Angeles", "CA")],
        "westseattlewa": by_name[("Seattle", "WA")],
        "bullcity": by_name[("Durham", "NC")],
        "fountaincolorado": by_name[("Fountain", "CO")],
        "littleton": by_name[("Littleton", "CO")],
        "eugene": by_name[("Eugene", "OR")],
        "sanmateo": by_name[("San Mateo", "CA")],
        "glendale": None,
        "iowacity": by_name[("Iowa City", "IA")],
        "eastpaloalto": by_name[("East Palo Alto", "CA")],
        "cecilcounty": by_name[("Cecil County", "MD")],
        "vancouver": vancouver,
    }
