"""Link Wikidata subreddit statements to local places.

Wikidata items for places carry a subreddit property (``P3984``). This module
reads a SPARQL response, matches each item label against a local
:class:`~watch.models.Place` name (or an alias when no exact name matches),
and records the link on :class:`~watch.models.Subreddit`.

The loader is deliberately conservative: a statement is only written when
exactly one place matches after using the item's state and country labels to
disambiguate. Ambiguous and unmatched items are counted, never guessed, and
operator-created ``manual`` rows are never touched.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import httpx
from django.db.models import Q, QuerySet
from django_countries import countries

from watch.models import Place, PlaceAlias, Subreddit
from watch.normalize import (
    PROVINCE_NAMES,
    STATE_FIPS,
    STATE_NAMES,
    normalize_key,
    state_name,
)

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "civicband-watch/0.1"

SPARQL_QUERY = """
SELECT ?item ?itemLabel ?subreddit ?geonamesId ?stateLabel ?countryLabel WHERE {
  ?item wdt:P3984 ?subreddit .
  OPTIONAL { ?item wdt:P1566 ?geonamesId . }
  OPTIONAL { ?item wdt:P131 ?state . }
  OPTIONAL { ?item wdt:P17 ?country . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
""".strip()

# Full state and province names mapped to the codes stored on ``Place.state``.
_STATE_CODES: dict[str, str] = {
    normalize_key(state_name(code)): code
    for code in (*STATE_FIPS.values(), *PROVINCE_NAMES)
}

# A few common Wikidata labels whose English short form is not the django-
# countries name. Unrecognized labels are ignored rather than guessed.
_COUNTRY_ALIASES: dict[str, str] = {
    "unitedstates": "US",
    "usa": "US",
    "us": "US",
    "uk": "GB",
    "unitedkingdom": "GB",
    "greatbritain": "GB",
}

STAT_KEYS: tuple[str, ...] = ("linked", "unmatched", "skipped")

Fetch = Callable[[str], dict]


def load_wikidata(
    *, endpoint: str = WIKIDATA_ENDPOINT, fetch: Fetch | None = None
) -> dict[str, int]:
    """Load Wikidata subreddit links and return linked/unmatched/skipped counts.

    ``fetch`` receives the endpoint and returns the decoded SPARQL JSON
    response. Tests inject a fixture; the default performs an HTTP request.
    """
    stats = dict.fromkeys(STAT_KEYS, 0)
    payload = (fetch or _fetch_payload)(endpoint)

    rows = parse_wikidata_bindings(payload)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["subreddit"], []).append(row)

    # Build the index from the binding labels only. Loading every place and
    # alias in the gazetteer costs hundreds of megabytes once the real data is
    # in place, and this loader only ever looks up these keys.
    index = _PlaceIndex(
        keys={normalize_key(row["place_name"]) for row in rows},
        names={row["place_name"] for row in rows if row["place_name"]},
    )
    for name, rows in grouped.items():
        if not name:
            stats["skipped"] += 1
            continue

        existing = Subreddit.objects.filter(name__iexact=name).first()
        if existing is not None and existing.source == Subreddit.Source.MANUAL:
            stats["skipped"] += 1
            continue

        # A single item can produce several SPARQL rows (for example one per
        # P131 value). The whole subreddit is unmatched unless every row
        # resolves and all of them agree on exactly one place.
        resolved: list[Place] = []
        for row in rows:
            place = index.match(
                row["place_name"],
                _state_code(row["state"]),
                row["country"],
            )
            if place is None:
                resolved = []
                break
            resolved.append(place)

        places = {str(place.pk) for place in resolved}
        if len(places) != 1:
            stats["unmatched"] += 1
            continue

        place = resolved[0]
        defaults: dict[str, object] = {
            "kind": Subreddit.Kind.PLACE,
            "place": place,
            "confidence": Subreddit.Confidence.HIGH,
            "source": Subreddit.Source.WIKIDATA,
        }
        if place.state:
            defaults["state"] = place.state
        if existing is None:
            defaults["reviewed_at"] = None
        Subreddit.objects.update_or_create(
            name=existing.name if existing is not None else name,
            defaults=defaults,
        )
        stats["linked"] += 1

    return stats


def parse_wikidata_bindings(payload: dict) -> list[dict]:
    """Return ``subreddit``/``place_name``/``state``/``country`` rows."""
    results = (payload.get("results") or {}) if isinstance(payload, dict) else {}
    bindings = results.get("bindings") or []

    rows: list[dict] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        rows.append(
            {
                "subreddit": normalize_subreddit(_binding_value(binding, "subreddit")),
                "place_name": _binding_value(binding, "placeLabel")
                or _binding_value(binding, "itemLabel"),
                "state": _binding_value(binding, "stateLabel"),
                "country": _binding_value(binding, "countryLabel"),
            }
        )
    return rows


def normalize_subreddit(value: str) -> str:
    """Return a bare, lower-case subreddit name (``r/Foo`` becomes ``foo``)."""
    name = " ".join(value.split())
    if name[:2].lower() == "r/":
        name = name[2:]
    return name.strip().lower()


def _binding_value(binding: dict, key: str) -> str:
    """Return one SPARQL binding value, whitespace-collapsed."""
    value = binding.get(key)
    if not isinstance(value, dict):
        return ""
    return " ".join(str(value.get("value", "")).split())


def _state_code(label: str) -> str:
    """Return a state or province code for a label, or an empty string."""
    text = " ".join(label.split())
    if not text:
        return ""
    code = text.upper()
    if code in STATE_NAMES or code in PROVINCE_NAMES:
        return code
    return _STATE_CODES.get(normalize_key(text), "")


def _country_code(label: str) -> str:
    """Return an ISO country code for a label, or an empty string."""
    text = " ".join(label.split())
    if not text:
        return ""
    key = normalize_key(text)
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    return countries.by_name(text) or ""


def _fetch_payload(endpoint: str) -> dict:
    """Fetch a SPARQL JSON response from ``endpoint``."""
    response = httpx.get(
        endpoint,
        params={"query": SPARQL_QUERY, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


class _PlaceIndex:
    """Lazily indexed place names and aliases for exact normalized matching.

    The index is scoped to the normalized keys and raw labels present in the
    Wikidata bindings. Place names are queried with a case-insensitive label
    filter; aliases are queried by ``key__in``. State and country filters are
    still applied per binding in :meth:`match`.
    """

    def __init__(self, *, keys: Iterable[str], names: Iterable[str] = ()) -> None:
        self._keys = frozenset(key for key in keys if key)
        self._labels = frozenset(name for name in names if name)
        self._name_map: dict[str, list[Place]] | None = None
        self._alias_map: dict[str, list[Place]] | None = None

    def match(self, place_name: str, state: str, country_label: str) -> Place | None:
        """Return the one place matching ``place_name``, or ``None``.

        Exact normalized place names take precedence over aliases so a county's
        stripped base alias cannot shadow an eponymous city. When every name
        candidate is filtered out by state or country, aliases are retried.
        State and country codes narrow the candidates when present; an
        unmapped state label is ignored, while a non-empty country label that
        cannot be resolved is a hard non-match.
        """
        key = normalize_key(place_name)
        if not key:
            return None

        country = _country_code(country_label)
        if country_label and not country:
            return None

        matches = self._filter(self._name_index().get(key, []), state, country)
        if not matches:
            matches = self._filter(self._alias_index().get(key, []), state, country)

        unique = {str(place.pk): place for place in matches}
        return next(iter(unique.values())) if len(unique) == 1 else None

    @staticmethod
    def _filter(matches: list[Place], state: str, country: str) -> list[Place]:
        """Narrow candidates by state and country codes when present."""
        if state:
            matches = [place for place in matches if place.state == state]
        if country:
            matches = [place for place in matches if str(place.country) == country]
        return matches

    def _name_index(self) -> dict[str, list[Place]]:
        if self._name_map is None:
            self._name_map = _build_index(
                (normalize_key(place.name), place)
                for place in _place_queryset(self._labels)
            )
        return self._name_map

    def _alias_index(self) -> dict[str, list[Place]]:
        if self._alias_map is None:
            self._alias_map = _build_index(
                (normalize_key(alias.key), alias.place)
                for alias in _alias_queryset(self._keys)
            )
        return self._alias_map


def _place_queryset(labels: Iterable[str]) -> QuerySet[Place]:
    """Return only places whose names could match the binding labels."""
    values = [label for label in labels if label]
    if not values:
        return Place.objects.none()
    query = Q()
    for value in values:
        query |= Q(name__iexact=value)
    return Place.objects.filter(query)


def _alias_queryset(keys: Iterable[str]) -> QuerySet[PlaceAlias]:
    """Return only aliases whose normalized key appears in the bindings."""
    values = sorted({key for key in keys if key})
    return PlaceAlias.objects.filter(key__in=values).select_related("place")


def _build_index(pairs: Iterable[tuple[str, Place]]) -> dict[str, list[Place]]:
    """Group places by a non-empty key."""
    index: dict[str, list[Place]] = {}
    for key, place in pairs:
        if key:
            index.setdefault(str(key), []).append(place)
    return index
