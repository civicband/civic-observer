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

from watch.models import Place, PlaceAlias
from watch.normalize import (
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
