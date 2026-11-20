"""Fixture data for the layered subreddit classifier tests.

The fixture mirrors the gazetteer rows that the real loader would create: one
:class:`~watch.models.Place` per city or county named in
``watch/data/subreddits.csv``, generated aliases, manual nicknames, and the
non-US city lookup rows needed by the hand-reviewed labels.
"""

from __future__ import annotations

import csv
from pathlib import Path

from watch.gazetteer import refresh_aliases
from watch.models import NonUsCity, NonUsCityAlias, Place, PlaceAlias
from watch.normalize import normalize_key

SUBREDDITS_CSV = (
    Path(__file__).resolve().parents[3] / "watch" / "data" / "subreddits.csv"
)

#: Rows whose hand label cannot be recovered from the generated gazetteer
#: aliases, or whose real populations make the place genuinely ambiguous.
AMBIGUOUS_ROWS: frozenset[str] = frozenset(
    {"glendale", "westseattlewa", "bullcity", "portland"}
)

#: Country expected for each non-US hand label.
NON_US_COUNTRIES: dict[str, str] = {
    "irelandtransport": "IE",
    "loveBelfast": "GB",
    "galway": "IE",
    "housingireland": "IE",
    "brisbane": "AU",
    "aussiememes": "AU",
    "auckland": "NZ",
    "brum": "GB",
    "theunitedkingdomnews": "GB",
    "kingstonontario": "CA",
    "vancouverlandlords": "CA",
}


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
    ("us:place:2360545", "Portland", "ME", 68408),
    # Cities whose counties share the same normalized name. The county is
    # usually larger, so the classifier must prefer the settlement before the
    # 10x ambiguity rule runs.
    ("us:place:0600562", "Alameda", "CA", 78280),
    ("us:place:0648872", "Monterey", "CA", 30218),
    ("us:place:0672646", "Sonoma", "CA", 10739),
    ("us:place:0653980", "Orange", "CA", 139419),
    ("us:place:0608352", "Brisbane", "CA", 4270),
    ("us:place:3628073", "Galway", "NY", 200),
    ("us:place:3957738", "Oregon", "OH", 20291),
    ("us:place:5559900", "Oregon", "WI", 9531),
    ("us:place:1756486", "Oregon", "IL", 3600),
    ("us:place:4213200", "Chester", "PA", 32605),
    ("us:place:5174000", "Richmond", "VA", 226610),
    ("us:place:5374100", "Vancouver", "WA", 190915),
    ("us:place:4875428", "Victoria", "TX", 65702),
    ("us:place:0997660", "Windsor", "CT", 29192),
)

#: ``(canonical_key, name, province, population)`` for Canadian cities.
_CANADIAN_CITIES: tuple[tuple[str, str, str, int], ...] = (
    ("ca:place:6173331", "Vancouver", "BC", 662248),
    ("ca:place:5913490", "Richmond", "BC", 209937),
    ("ca:place:5911606", "Victoria", "BC", 91867),
    ("ca:place:6183235", "Windsor", "ON", 229660),
)

#: ``(canonical_key, name, state, population)`` for county rows.
_COUNTIES: tuple[tuple[str, str, str, int], ...] = (
    ("us:county:24015", "Cecil County", "MD", 103725),
    ("us:county:42029", "Chester County", "PA", 534413),
    # Same-named counties for the hand-labelled CSV places. Their populations
    # are realistic and several are larger than the eponymous city.
    ("us:county:06037", "Los Angeles County", "CA", 10014009),
    ("us:county:06073", "San Diego County", "CA", 3298634),
    ("us:county:55063", "La Crosse County", "WI", 120784),
    ("us:county:06081", "San Mateo County", "CA", 764442),
    ("us:county:06001", "Alameda County", "CA", 1682353),
    ("us:county:06053", "Monterey County", "CA", 439035),
    ("us:county:06097", "Sonoma County", "CA", 488863),
    ("us:county:06059", "Orange County", "CA", 3186989),
    ("us:county:06007", "Butte County", "CA", 211632),
)

#: ``(geoname_id, name, country, admin1, population)`` for non-US cities.
_NON_US_CITIES: tuple[tuple[int, str, str, str, int], ...] = (
    (2643743, "London", "GB", "ENG", 8961989),
    (2964574, "Dublin", "IE", "L", 1173179),
    (2655984, "Belfast", "GB", "NIR", 333871),
    (2193733, "Auckland", "NZ", "AUK", 417910),
    (6173331, "Vancouver", "CA", "BC", 662248),
    (6122085, "Richmond", "CA", "BC", 209937),
    (6174041, "Victoria", "CA", "BC", 91867),
    (6182959, "Windsor", "CA", "ON", 229660),
    (2174003, "Brisbane", "AU", "QLD", 2560720),
    (2964180, "Galway", "IE", "C", 85400),
    (2655603, "Birmingham", "GB", "ENG", 1144919),
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
    for canonical_key, name, state, population in _COUNTIES:
        by_name[(name, state)] = _make_place(
            canonical_key,
            name,
            state,
            place_type=Place.PlaceType.COUNTY,
            population=population,
        )

    canadian: dict[tuple[str, str], Place] = {}
    for canonical_key, name, province, population in _CANADIAN_CITIES:
        canadian[(name, province)] = _make_place(
            canonical_key,
            name,
            province,
            place_type=Place.PlaceType.CANADIAN_CITY,
            country="CA",
            population=population,
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
    NonUsCityAlias.objects.create(
        city=NonUsCity.objects.get(name="Birmingham"),
        key="brum",
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
        # Portland ME makes the real OR/ME ratio just under 10x.
        "portland": None,
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
        "vancouver": canadian[("Vancouver", "BC")],
    }


def create_classifier_labels(
    places: dict[str, Place | None],
) -> dict[str, dict[str, object]]:
    """Return an expectation for every row in the hand-label CSV.

    The kind and state columns come from the CSV; place rows use the fixture
    place map, and the explicit ambiguous set records rows the gazetteer
    cannot resolve without operator data.
    """
    with SUBREDDITS_CSV.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    labels: dict[str, dict[str, object]] = {}
    for row in rows:
        name = row["subreddit"]
        kind = row["kind"]
        if kind == "place":
            if name in AMBIGUOUS_ROWS:
                labels[name] = {"kind": "ambiguous", "place": None}
            else:
                labels[name] = {"kind": "place", "place": places[name]}
        elif kind == "state":
            labels[name] = {"kind": "state", "state": row["state"]}
        elif kind == "region":
            labels[name] = {"kind": "region", "state": row["state"] or ""}
        elif kind == "non_us":
            labels[name] = {"kind": "non_us", "country": NON_US_COUNTRIES[name]}
        else:
            labels[name] = {"kind": "general"}
    return labels
