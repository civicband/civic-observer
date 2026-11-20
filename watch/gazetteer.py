"""Load Census Gazetteer files into typed :class:`~watch.models.Place` rows.

The loader is idempotent: places are upserted by their namespaced
``canonical_key`` and generated ``gazetteer`` aliases are rebuilt on every run.
Aliases from other sources, including operator-created ``manual`` aliases, are
never touched.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from watch.models import NonUsCity, NonUsCityAlias, Place, PlaceAlias
from watch.normalize import (
    STATE_FIPS,
    clean_name,
    download,
    normalize_key,
    state_from_fips,
    state_name,
)

GAZETTEER_BASE_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{year}_Gazetteer"
)

# File stems after ``<year>_Gaz_``. School districts are split into three
# files by census vintage; all three are loaded under the ``school`` count.
GAZETTEER_STEMS: tuple[str, ...] = (
    "state",
    "counties",
    "cousubs",
    "place",
    "elsd",
    "scsd",
    "unsd",
)

# Gazetteer LSAD codes mapped to the legal suffix that appears in NAME.
LSAD_SUFFIX: dict[str, str] = {
    "25": "city",
    "43": "town",
    "153": "town",
    "47": "village",
    "57": "CDP",
    "21": "city",
    "53": "city",
    "county": "county",
    "cousub": "township",
    "school": "school district",
}

# Gazetteer LSAD codes for incorporated places and CDPs.
LSAD_PLACE_TYPE: dict[str, str] = {
    "25": Place.PlaceType.CITY,
    "43": Place.PlaceType.TOWN,
    "153": Place.PlaceType.TOWN,
    "47": Place.PlaceType.VILLAGE,
    "57": Place.PlaceType.CDP,
    "21": Place.PlaceType.CITY,
    "53": Place.PlaceType.CITY,
}

# Suffix used for the type-specific alias ("abbevillecity", "abilenetown").
TYPE_ALIAS_SUFFIX: dict[str, str] = {
    Place.PlaceType.CITY: "city",
    Place.PlaceType.TOWN: "town",
    Place.PlaceType.VILLAGE: "village",
    Place.PlaceType.CDP: "cdp",
}

# Legal suffixes found in official county and county-subdivision names. They
# are stripped only when generating aliases, never from ``Place.name``:
# "Baltimore city" and "Carson City" are official names.
KNOWN_LEGAL_SUFFIXES: tuple[str, ...] = (
    "county",
    "parish",
    "borough",
    "census area",
    "municipality",
    "city",
    "town",
    "township",
    "village",
    "CDP",
    "CCD",
    "UT",
    "city and borough",
    "city and county",
    "unified government",
)

_KNOWN_SUFFIX_PATTERNS = tuple(
    re.compile(rf"\s+{re.escape(suffix)}$", re.IGNORECASE)
    for suffix in sorted(KNOWN_LEGAL_SUFFIXES, key=len, reverse=True)
)


class GazetteerError(ValueError):
    """Raised when a Gazetteer file cannot be parsed."""


class GovernmentError(GazetteerError):
    """Raised when a Census of Governments file cannot be read."""


@dataclass(frozen=True)
class GazetteerDataset:
    """Describe one Gazetteer file and the places it contains."""

    stem: str
    count_key: str
    key_prefix: str
    place_type: str | None
    suffix: str | None
    # County and county-subdivision names are official and keep their suffix.
    official_name: bool = False


DATASETS: tuple[GazetteerDataset, ...] = (
    GazetteerDataset("state", "state", "us:state:", Place.PlaceType.STATE, None),
    GazetteerDataset(
        "counties",
        "county",
        "us:county:",
        Place.PlaceType.COUNTY,
        None,
        official_name=True,
    ),
    GazetteerDataset(
        "cousubs",
        "cousub",
        "us:cousub:",
        Place.PlaceType.COUNTY_SUBDIVISION,
        None,
        official_name=True,
    ),
    # Places derive their type and suffix from LSAD.
    GazetteerDataset("place", "place", "us:place:", None, None),
    GazetteerDataset(
        "elsd",
        "school",
        "us:school:",
        Place.PlaceType.SCHOOL_DISTRICT,
        "school district",
    ),
    GazetteerDataset(
        "scsd",
        "school",
        "us:school:",
        Place.PlaceType.SCHOOL_DISTRICT,
        "school district",
    ),
    GazetteerDataset(
        "unsd",
        "school",
        "us:school:",
        Place.PlaceType.SCHOOL_DISTRICT,
        "school district",
    ),
)

COUNT_KEYS: tuple[str, ...] = ("state", "county", "cousub", "place", "school")


def gazetteer_path(source_dir: Path, stem: str, *, year: int = 2026) -> Path:
    """Return the expected TSV path for one Gazetteer dataset."""
    return Path(source_dir) / f"{year}_Gaz_{stem}_national.txt"


def missing_stems(source_dir: Path, *, year: int = 2026) -> list[str]:
    """Return the dataset stems whose TSV is absent from ``source_dir``."""
    return [
        stem
        for stem in GAZETTEER_STEMS
        if not gazetteer_path(source_dir, stem, year=year).is_file()
    ]


def load_gazetteer(source_dir: Path, *, year: int = 2026) -> dict[str, int]:
    """Load every Gazetteer TSV in ``source_dir`` and return row counts.

    Missing files are skipped so partial downloads can load only the datasets
    they contain. The management command reports which stems are missing.
    """
    counts = dict.fromkeys(COUNT_KEYS, 0)
    for dataset in DATASETS:
        path = gazetteer_path(source_dir, dataset.stem, year=year)
        if not path.is_file():
            continue
        for row in _read_rows(path):
            record = _place_record(row, dataset)
            if record is None:
                continue
            geoid, fields = record
            place = upsert_place(dataset.key_prefix + geoid, **fields)
            refresh_aliases(place)
            counts[dataset.count_key] += 1
    return counts


def upsert_place(canonical_key: str, **fields) -> Place:
    """Create or update a place by its namespaced canonical key."""
    place, _ = Place.objects.update_or_create(
        canonical_key=canonical_key, defaults=fields
    )
    return place


def build_aliases(place: Place) -> list[PlaceAlias]:
    """Return unsaved Gazetteer aliases for ``place``.

    Includes the display name, name plus state, name plus full state name, a
    type-specific key when one applies, known-suffix-stripped bases for county
    and county-subdivision names, and the legal name as a ``variant`` alias
    when it differs from the display name.
    """
    aliases: dict[tuple[str, str], PlaceAlias] = {}

    def add(kind: str, value: str) -> None:
        key = normalize_key(value)
        if key and (key, kind) not in aliases:
            aliases[(key, kind)] = PlaceAlias(
                place=place, key=key, kind=kind, source=PlaceAlias.Source.GAZETTEER
            )

    add(PlaceAlias.Kind.NAME, place.name)

    full_state = ""
    if place.state:
        add(PlaceAlias.Kind.NAME_STATE, f"{place.name} {place.state}")
        try:
            full_state = state_name(place.state)
        except KeyError:
            full_state = ""
        if full_state:
            add(PlaceAlias.Kind.NAME_STATE_NAME, f"{place.name} {full_state}")

    if place.place_type in {
        Place.PlaceType.COUNTY,
        Place.PlaceType.COUNTY_SUBDIVISION,
    }:
        base_name = _strip_known_legal_suffix(place.name)
        if normalize_key(base_name) != normalize_key(place.name):
            add(PlaceAlias.Kind.NAME, base_name)
            if place.state:
                add(PlaceAlias.Kind.NAME_STATE, f"{base_name} {place.state}")
                if full_state:
                    add(
                        PlaceAlias.Kind.NAME_STATE_NAME,
                        f"{base_name} {full_state}",
                    )
    else:
        type_suffix = TYPE_ALIAS_SUFFIX.get(place.place_type)
        if type_suffix and not normalize_key(place.name).endswith(
            normalize_key(type_suffix)
        ):
            add(PlaceAlias.Kind.NAME_CITY, f"{place.name} {type_suffix}")

    if place.legal_name and normalize_key(place.legal_name) != normalize_key(
        place.name
    ):
        add(PlaceAlias.Kind.VARIANT, place.legal_name)

    return list(aliases.values())


def refresh_aliases(place: Place) -> int:
    """Replace generated aliases for ``place`` and return the number created.

    Only ``source="gazetteer"`` aliases are replaced. Manual, GeoNames, and
    Wikidata aliases survive, including when one shares a key and kind with a
    generated alias.
    """
    preserved_pairs = set(
        place.aliases.exclude(source=PlaceAlias.Source.GAZETTEER).values_list(
            "key", "kind"
        )
    )
    place.aliases.filter(source=PlaceAlias.Source.GAZETTEER).delete()

    aliases = [
        alias
        for alias in build_aliases(place)
        if (alias.key, alias.kind) not in preserved_pairs
    ]
    PlaceAlias.objects.bulk_create(aliases, batch_size=1000)
    return len(aliases)


def _strip_known_legal_suffix(name: str) -> str:
    """Return ``name`` without one trailing known legal suffix."""
    normalized = " ".join(name.split())
    for pattern in _KNOWN_SUFFIX_PATTERNS:
        stripped = pattern.sub("", normalized).strip()
        if stripped and stripped != normalized:
            return stripped
    return normalized


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Parse a Gazetteer TSV with pipe or tab delimiters.

    Leading blank lines are ignored. Raises :class:`GazetteerError` when the
    file is empty, has no recognizable delimiter, or lacks the required header
    columns.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise GazetteerError(f"Cannot read Gazetteer file {path}: {exc}") from exc

    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise GazetteerError(f"Gazetteer file is empty: {path}")

    header = lines[0]
    if "|" in header:
        delimiter = "|"
    elif "\t" in header:
        delimiter = "\t"
    else:
        raise GazetteerError(f"Cannot detect delimiter in Gazetteer file: {path}")

    reader = csv.DictReader(lines, delimiter=delimiter)
    fieldnames = {(name or "").strip() for name in reader.fieldnames or []}
    if not {"GEOID", "NAME"} <= fieldnames:
        raise GazetteerError(f"Gazetteer file {path} is missing GEOID or NAME columns")

    rows: list[dict[str, str]] = []
    for raw in reader:
        if None in raw:
            raise GazetteerError(
                f"Gazetteer file {path} has a row with more fields than its header"
            )
        row = {
            str(key).strip(): (value or "").strip()
            for key, value in raw.items()
            if isinstance(key, str)
        }
        if any(row.values()):
            rows.append(row)
    return rows


def _place_record(
    row: dict[str, str], dataset: GazetteerDataset
) -> tuple[str, dict] | None:
    """Turn one Gazetteer row into a GEOID and ``Place`` field mapping."""
    geoid = row.get("GEOID", "")
    legal_name = row.get("NAME", "")
    if not geoid or not legal_name:
        return None

    lsad = row.get("LSAD", "")
    if dataset.place_type is None:
        place_type = LSAD_PLACE_TYPE.get(lsad)
        if place_type is None:
            # Unknown LSAD: keep the official name and fall back to city
            # rather than silently stripping a suffix we cannot identify.
            place_type = Place.PlaceType.CITY
            name = " ".join(legal_name.split())
        else:
            name = clean_name(legal_name, suffix=LSAD_SUFFIX.get(lsad))
    elif dataset.official_name:
        # County and county-subdivision names are official ("Baltimore city",
        # "Carson City"); strip legal suffixes only in aliases.
        place_type = dataset.place_type
        name = " ".join(legal_name.split())
    else:
        place_type = dataset.place_type
        name = clean_name(legal_name, suffix=dataset.suffix)

    state = row.get("USPS", "").upper()
    if not state:
        try:
            state = state_from_fips(geoid[:2])
        except KeyError:
            state = ""

    fields = {
        "geoid": geoid,
        "name": name,
        "legal_name": legal_name,
        "place_type": place_type,
        "state": state,
        "country": "US",
        "latitude": _parse_float(row.get("INTPTLAT")),
        "longitude": _parse_float(row.get("INTPTLONG")),
    }
    return geoid, fields


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Census of Governments
# ---------------------------------------------------------------------------

GOVERNMENT_FILENAME = "Govt_Units_2022_Final.xlsx"
GOVERNMENT_SHEETS: tuple[str, ...] = ("General Purpose", "School District")
GOVERNMENT_COUNT_KEYS: tuple[str, ...] = (
    "county",
    "municipal",
    "township",
    "school",
    "unmatched_school",
    "skipped",
)

UNIT_COUNTY = "1 - COUNTY"
UNIT_MUNICIPAL = "2 - MUNICIPAL"
UNIT_TOWNSHIP = "3 - TOWNSHIP"
UNIT_SCHOOL = "SCHOOL DISTRICT"

_UNIT_TYPE_BUCKET: dict[str, str] = {
    UNIT_COUNTY: "county",
    UNIT_MUNICIPAL: "municipal",
    UNIT_TOWNSHIP: "township",
    UNIT_SCHOOL: "school",
}


def government_key(unit_type: str, state: str, county: str, place: str) -> str | None:
    """Return the canonical key a government unit row points at.

    Returns ``None`` for unit types that have no direct Gazetteer match, such
    as special districts, or when a required FIPS component is missing.
    """
    bucket = _UNIT_TYPE_BUCKET.get(_unit_type(unit_type))
    state = (state or "").strip()
    county = (county or "").strip()
    place = (place or "").strip()

    if bucket == "county" and state and county:
        return f"us:county:{state}{county}"
    if bucket == "municipal" and state and place:
        return f"us:place:{state}{place}"
    if bucket == "township" and state and county and place:
        return f"us:cousub:{state}{county}{place}"
    return None


def load_government_rows(rows: Iterable[dict]) -> dict[str, int]:
    """Flag Census of Governments units and fill population and county.

    Rows are already-normalized dicts with ``unit_type``, ``fips_state``,
    ``fips_county``, ``fips_place``, ``unit_name``, ``population``, and
    ``is_active`` keys. Inactive rows, unknown unit types, and units without a
    matching place are counted as ``skipped``. School districts that match no
    place or more than one place are counted as ``unmatched_school``.
    """
    stats = dict.fromkeys(GOVERNMENT_COUNT_KEYS, 0)
    school_index: dict[tuple[str, str], list[Place]] | None = None
    county_cache: dict[str, Place | None] = {}

    for row in rows:
        if _cell(row, "is_active").upper() != "Y":
            stats["skipped"] += 1
            continue

        unit_type = _cell(row, "unit_type")
        bucket = _UNIT_TYPE_BUCKET.get(_unit_type(unit_type))
        if bucket is None:
            stats["skipped"] += 1
            continue

        if bucket == "school":
            if school_index is None:
                school_index = _build_school_index()
            place = _match_school_district(row, school_index)
            if place is None:
                stats["unmatched_school"] += 1
                continue
        else:
            key = government_key(
                unit_type,
                _fips(row, "fips_state", 2),
                _fips(row, "fips_county", 3),
                _fips(row, "fips_place", 5),
            )
            place = Place.objects.filter(canonical_key=key).first() if key else None
            if place is None:
                stats["skipped"] += 1
                continue

        _apply_government(place, row, county_cache)
        stats[bucket] += 1

    return stats


def load_governments(xlsx_path: Path) -> dict[str, int]:
    """Load the General Purpose and School District sheets of a units XLSX."""
    return load_government_rows(_government_rows(Path(xlsx_path)))


def _government_rows(path: Path) -> Iterator[dict]:
    """Yield normalized rows from the supported sheets of a units XLSX."""
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, InvalidFileException, zipfile.BadZipFile) as exc:
        raise GovernmentError(
            f"Cannot read Census of Governments file {path}: {exc}"
        ) from exc

    try:
        missing = [
            name for name in GOVERNMENT_SHEETS if name not in workbook.sheetnames
        ]
        if missing:
            raise GovernmentError(
                f"Census of Governments file {path} is missing sheets: "
                + ", ".join(missing)
            )
        for sheet_name in GOVERNMENT_SHEETS:
            yield from _government_sheet_rows(
                workbook[sheet_name], school=sheet_name == "School District"
            )
    finally:
        workbook.close()


def _government_sheet_rows(worksheet, *, school: bool) -> Iterator[dict]:
    """Yield normalized rows from one worksheet."""
    rows = worksheet.iter_rows(values_only=True)
    header_values = next(rows, None)
    if header_values is None:
        raise GovernmentError(f"Government sheet {worksheet.title!r} is empty")

    header = [
        "" if value is None else str(value).strip().upper() for value in header_values
    ]
    for values in rows:
        if not any(value not in (None, "") for value in values):
            continue
        raw = {name: value for name, value in zip(header, values, strict=False) if name}
        yield {
            "unit_type": UNIT_SCHOOL if school else _cell(raw, "UNIT_TYPE"),
            "unit_name": _cell(raw, "UNIT_NAME"),
            "fips_state": _cell(raw, "FIPS_STATE"),
            "fips_county": _cell(raw, "FIPS_COUNTY"),
            "fips_place": _cell(raw, "FIPS_PLACE"),
            "population": _cell(raw, "POPULATION"),
            "is_active": _cell(raw, "IS_ACTIVE"),
        }


def _unit_type(value: object) -> str:
    """Return a whitespace-collapsed upper-case unit type."""
    if value is None:
        return ""
    return " ".join(str(value).split()).upper()


def _cell(row: dict, name: str) -> str:
    """Return a stripped string cell, tolerating absent or blank cells."""
    value = row.get(name)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _fips(row: dict, name: str, width: int) -> str:
    """Return a zero-padded FIPS component, or an empty string."""
    text = _cell(row, name)
    if not text.isdigit():
        return ""
    return text.zfill(width)


def _population(row: dict) -> int | None:
    """Return a positive population count, or ``None`` when absent or zero."""
    text = _cell(row, "population").replace(",", "")
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number > 0 else None


def _state_abbreviation(fips_state: str) -> str:
    """Return the USPS code for a state FIPS code, or an empty string."""
    try:
        return state_from_fips(fips_state)
    except KeyError:
        return ""


def _school_name_key(name: str) -> str:
    """Return the comparison key used to match school districts."""
    return normalize_key(clean_name(name, suffix="school district"))


def _build_school_index() -> dict[tuple[str, str], list[Place]]:
    """Index school districts by state and normalized name."""
    index: dict[tuple[str, str], list[Place]] = {}
    schools = Place.objects.filter(place_type=Place.PlaceType.SCHOOL_DISTRICT)
    for place in schools:
        index.setdefault((place.state, _school_name_key(place.name)), []).append(place)
    return index


def _match_school_district(
    row: dict, index: dict[tuple[str, str], list[Place]]
) -> Place | None:
    """Return the unique school district match for ``row``, if any."""
    state = _state_abbreviation(_fips(row, "fips_state", 2))
    key = _school_name_key(_cell(row, "unit_name"))
    if not state or not key:
        return None
    matches = index.get((state, key), [])
    return matches[0] if len(matches) == 1 else None


def _apply_government(
    place: Place, row: dict, county_cache: dict[str, Place | None]
) -> None:
    """Persist government flag, population, and county for ``place``."""
    fields: dict[str, object] = {"is_government": True}
    population = _population(row)
    if population is not None:
        fields["population"] = population
    if place.place_type != Place.PlaceType.COUNTY:
        county = _county_for_row(row, county_cache)
        if county is not None:
            fields["county"] = county

    for name, value in fields.items():
        setattr(place, name, value)
    place.save(update_fields=[*fields, "modified"])


def _county_for_row(row: dict, county_cache: dict[str, Place | None]) -> Place | None:
    """Return the county place named by a row's FIPS codes, if one exists."""
    state = _fips(row, "fips_state", 2)
    county = _fips(row, "fips_county", 3)
    if not state or not county:
        return None

    key = f"us:county:{state}{county}"
    if key not in county_cache:
        county_cache[key] = Place.objects.filter(canonical_key=key).first()
    return county_cache[key]


def download_gazetteer(*, year: int = 2026, directory: Path) -> list[Path]:
    """Download and extract every Gazetteer zip for ``year`` into ``directory``."""
    base = GAZETTEER_BASE_URL.format(year=year)
    extracted: list[Path] = []
    for stem in GAZETTEER_STEMS:
        archive_name = f"{year}_Gaz_{stem}_national.zip"
        archive = download(f"{base}/{archive_name}", archive_name)
        extracted.extend(_extract_zip(archive, Path(directory)))
    return extracted


def _extract_zip(archive: Path, directory: Path) -> list[Path]:
    """Extract flat file members of ``archive`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            if member.is_dir():
                continue
            name = Path(member.filename).name
            if not name or name.startswith("."):
                continue
            target = directory / name
            with zipped.open(member) as source, target.open("wb") as destination:
                destination.write(source.read())
            written.append(target)
    return written


# ---------------------------------------------------------------------------
# GeoNames cities500
# ---------------------------------------------------------------------------

GEONAMES_CITIES_URL = "https://download.geonames.org/export/dump/cities500.zip"
GEONAMES_CITIES_FILENAME = "cities500.txt"
GEONAMES_COLUMN_COUNT = 19
DEFAULT_MIN_POPULATION = 15000

GEONAMES_COUNT_KEYS: tuple[str, ...] = (
    "canadian_places",
    "non_us_cities",
    "aliases",
    "counties_filled",
    "skipped",
)

# GeoNames admin1 values are state or province codes, so US counties can be
# resolved exactly from the state FIPS code plus the admin2 county FIPS code.
_US_STATE_FIPS: dict[str, str] = {code: fips for fips, code in STATE_FIPS.items()}

# Settlement types that can take a county from a GeoNames cities row. States,
# counties, and school districts are excluded so an eponymous county or school
# district does not make every city match ambiguous.
_US_SETTLEMENT_TYPES: tuple[str, ...] = (
    Place.PlaceType.CITY,
    Place.PlaceType.TOWN,
    Place.PlaceType.VILLAGE,
    Place.PlaceType.CDP,
    Place.PlaceType.COUNTY_SUBDIVISION,
)


@dataclass(frozen=True)
class GeonamePlace:
    """One parsed row from a GeoNames cities file."""

    geoname_id: int
    name: str
    alternatenames: tuple[str, ...]
    feature_class: str
    country: str
    admin1: str
    admin2: str
    latitude: float | None
    longitude: float | None
    population: int


def geonames_path(directory: Path) -> Path:
    """Return the expected path of ``cities500.txt`` inside ``directory``."""
    return Path(directory) / GEONAMES_CITIES_FILENAME


def download_geonames(directory: Path) -> Path:
    """Download and extract the GeoNames cities file into ``directory``."""
    archive = download(GEONAMES_CITIES_URL, "cities500.zip")
    _extract_zip(archive, Path(directory))
    target = geonames_path(directory)
    if not target.is_file():
        raise GazetteerError(
            f"GeoNames archive did not contain {GEONAMES_CITIES_FILENAME}"
        )
    return target


def load_geonames(
    cities_path: Path, *, min_population: int = DEFAULT_MIN_POPULATION
) -> dict[str, int]:
    """Load GeoNames cities, returning row and alias counts.

    Canadian cities become :class:`~watch.models.Place` rows and are also
    stored as :class:`~watch.models.NonUsCity` rows so mute-by-country and
    non-US lookup both work. Other non-US cities at or above ``min_population``
    become lookup rows only. US rows never create places; they only fill a
    missing county, matched by normalized name and state, from ``admin2``.
    """
    stats = dict.fromkeys(GEONAMES_COUNT_KEYS, 0)
    path = Path(cities_path)

    county_cache: dict[str, Place | None] = {}
    county_candidates: dict[tuple[str, str], dict[str, Place]] = {}
    county_index: dict[tuple[str, str], list[Place]] | None = None
    place_index: dict[tuple[str, str], list[Place]] | None = None

    try:
        handle = path.open(encoding="utf-8-sig")
    except OSError as exc:
        raise GazetteerError(f"Cannot read GeoNames file {path}: {exc}") from exc

    with handle:
        for line in handle:
            if not line.strip():
                continue

            row = _parse_geoname_line(line)
            if row is None or row.feature_class != "P":
                stats["skipped"] += 1
                continue

            if row.country == "US":
                if place_index is None:
                    place_index = _build_us_place_index()
                if county_index is None:
                    county_index = _build_county_index()
                _record_place_county(row, county_index, county_cache, county_candidates)
                continue

            if row.country == "CA":
                place = _upsert_canadian_place(row)
                stats["aliases"] += _refresh_geonames_place_aliases(
                    place, row.alternatenames
                )
                stats["canadian_places"] += 1
                city = _upsert_non_us_city(row)
                stats["aliases"] += _refresh_non_us_aliases(city, row.alternatenames)
                stats["non_us_cities"] += 1
                continue

            if row.population < min_population:
                stats["skipped"] += 1
                continue

            city = _upsert_non_us_city(row)
            stats["aliases"] += _refresh_non_us_aliases(city, row.alternatenames)
            stats["non_us_cities"] += 1

    if place_index is not None:
        stats["counties_filled"] = _apply_county_candidates(
            place_index, county_candidates
        )

    return stats


def build_geonames_aliases(
    place: Place, alternatenames: Iterable[str] = ()
) -> list[PlaceAlias]:
    """Return GeoNames aliases for a Canadian city.

    Aliases cover the name, name plus province code and full name, and every
    usable alternate name. Keys are deduplicated across kinds so an alternate
    name that repeats the city name is not stored twice.
    """
    aliases: dict[str, PlaceAlias] = {}

    def add(kind: str, value: str) -> None:
        key = _alias_key(value)
        if key and key not in aliases:
            aliases[key] = PlaceAlias(
                place=place, key=key, kind=kind, source=PlaceAlias.Source.GEONAMES
            )

    add(PlaceAlias.Kind.NAME, place.name)
    if place.state:
        add(PlaceAlias.Kind.NAME_STATE, f"{place.name} {place.state}")
        try:
            full_state = state_name(place.state)
        except KeyError:
            full_state = ""
        if full_state:
            add(PlaceAlias.Kind.NAME_STATE_NAME, f"{place.name} {full_state}")

    for alternate in alternatenames:
        add(PlaceAlias.Kind.VARIANT, alternate)

    return list(aliases.values())


def _refresh_geonames_place_aliases(place: Place, alternatenames: Iterable[str]) -> int:
    """Replace generated GeoNames aliases for a Canadian place.

    Aliases from other sources are preserved, including when one shares a
    ``(key, kind)`` pair with a generated alias; the generated duplicate is
    skipped rather than violating the unique constraint.
    """
    preserved = set(
        place.aliases.exclude(source=PlaceAlias.Source.GEONAMES).values_list(
            "key", "kind"
        )
    )
    place.aliases.filter(source=PlaceAlias.Source.GEONAMES).delete()
    aliases = [
        alias
        for alias in build_geonames_aliases(place, alternatenames)
        if (alias.key, alias.kind) not in preserved
    ]
    PlaceAlias.objects.bulk_create(aliases, batch_size=1000)
    return len(aliases)


def _upsert_canadian_place(row: GeonamePlace) -> Place:
    """Create or update the ``Place`` row for a Canadian city."""
    place, _ = Place.objects.update_or_create(
        canonical_key=f"ca:city:{row.geoname_id}",
        defaults={
            "name": row.name,
            "legal_name": row.name,
            "place_type": Place.PlaceType.CANADIAN_CITY,
            "state": row.admin1,
            "country": "CA",
            "latitude": row.latitude,
            "longitude": row.longitude,
            "population": row.population,
        },
    )
    return place


def _upsert_non_us_city(row: GeonamePlace) -> NonUsCity:
    """Create or update the lookup row for a non-US city."""
    city, _ = NonUsCity.objects.update_or_create(
        geoname_id=row.geoname_id,
        defaults={
            "name": row.name,
            "normalized_name": normalize_key(row.name)[:255],
            "country": row.country,
            "admin1": row.admin1,
            "population": row.population,
        },
    )
    return city


def _refresh_non_us_aliases(city: NonUsCity, alternatenames: Iterable[str]) -> int:
    """Replace the lookup aliases for one non-US city."""
    keys = _unique_alias_keys([city.name, *alternatenames])
    city.aliases.all().delete()
    NonUsCityAlias.objects.bulk_create(
        [NonUsCityAlias(city=city, key=key) for key in keys],
        batch_size=1000,
    )
    return len(keys)


def _unique_alias_keys(values: Iterable[str]) -> list[str]:
    """Return normalized, deduplicated alias keys in input order."""
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _alias_key(value)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _alias_key(value: str) -> str:
    """Return a normalized alias key capped at the model field length."""
    return normalize_key(value)[:255]


def _parse_geoname_line(line: str) -> GeonamePlace | None:
    """Parse one tab-separated GeoNames cities line.

    Returns ``None`` for blank, malformed, or incomplete rows so a single bad
    line does not abort a multi-hundred-thousand-row load.
    """
    text = line.rstrip("\r\n")
    if not text.strip():
        return None

    fields = text.split("\t")
    if len(fields) < GEONAMES_COLUMN_COUNT:
        return None
    try:
        geoname_id = int(fields[0])
    except ValueError:
        return None

    name = " ".join(fields[1].split())
    country = fields[8].strip().upper()
    if not name or len(country) != 2:
        return None

    alternatenames = tuple(
        part.strip() for part in fields[3].split(",") if part.strip()
    )
    return GeonamePlace(
        geoname_id=geoname_id,
        name=name,
        alternatenames=alternatenames,
        feature_class=fields[6].strip().upper(),
        country=country,
        admin1=fields[10].strip().upper(),
        admin2=fields[11].strip(),
        latitude=_parse_float(fields[4]),
        longitude=_parse_float(fields[5]),
        population=_parse_geoname_population(fields[14]),
    )


def _parse_geoname_population(value: str) -> int:
    """Return a non-negative population count from a GeoNames cell."""
    try:
        return max(0, int(value.strip() or 0))
    except ValueError:
        return 0


def _build_us_place_index() -> dict[tuple[str, str], list[Place]]:
    """Index US settlement places that have no county yet."""
    index: dict[tuple[str, str], list[Place]] = {}
    places = Place.objects.filter(
        country="US",
        county__isnull=True,
        place_type__in=_US_SETTLEMENT_TYPES,
    )
    for place in places:
        key = (place.state, normalize_key(place.name))
        if key[1]:
            index.setdefault(key, []).append(place)
    return index


def _build_county_index() -> dict[tuple[str, str], list[Place]]:
    """Index county places by state and normalized name and legal name."""
    index: dict[tuple[str, str], list[Place]] = {}
    for county in Place.objects.filter(place_type=Place.PlaceType.COUNTY):
        seen: set[str] = set()
        for name in (county.name, county.legal_name):
            key = normalize_key(name or "")
            if not key or key in seen:
                continue
            seen.add(key)
            index.setdefault((county.state, key), []).append(county)
    return index


def _county_from_admin2(
    row: GeonamePlace,
    county_index: dict[tuple[str, str], list[Place]],
    county_cache: dict[str, Place | None],
) -> Place | None:
    """Resolve a US row's ``admin2`` value to a loaded county, if unambiguous."""
    if not row.admin1 or not row.admin2:
        return None

    admin2 = row.admin2.strip()
    if admin2.isdigit():
        state_fips = _US_STATE_FIPS.get(row.admin1)
        if state_fips:
            key = f"us:county:{state_fips}{admin2.zfill(3)}"
            if key not in county_cache:
                county_cache[key] = Place.objects.filter(canonical_key=key).first()
            county = county_cache[key]
            if county is not None:
                return county

    # Some exports use a county name instead of a FIPS code.
    matches = county_index.get((row.admin1, normalize_key(admin2)), [])
    return matches[0] if len(matches) == 1 else None


def _record_place_county(
    row: GeonamePlace,
    county_index: dict[tuple[str, str], list[Place]],
    county_cache: dict[str, Place | None],
    county_candidates: dict[tuple[str, str], dict[str, Place]],
) -> None:
    """Collect the county a US row points at for its name and state.

    Nothing is written until every row has been read: two GeoNames rows with
    the same name and state but different counties must leave the place's
    county null rather than letting the first row win.
    """
    if not row.admin1:
        return

    county = _county_from_admin2(row, county_index, county_cache)
    if county is None:
        return

    key = (row.admin1, normalize_key(row.name))
    if not key[1]:
        return
    county_candidates.setdefault(key, {})[county.canonical_key] = county


def _apply_county_candidates(
    place_index: dict[tuple[str, str], list[Place]],
    county_candidates: dict[tuple[str, str], dict[str, Place]],
) -> int:
    """Write counties only where one place and one county are unambiguous."""
    filled = 0
    for key, candidates in county_candidates.items():
        matches = place_index.get(key, [])
        if len(matches) != 1 or len(candidates) != 1:
            continue

        place = matches[0]
        place.county = next(iter(candidates.values()))
        place.save(update_fields=["county", "modified"])
        filled += 1
    return filled
