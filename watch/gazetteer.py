"""Load Census Gazetteer files into typed :class:`~watch.models.Place` rows.

The loader is idempotent: places are upserted by their namespaced
``canonical_key`` and generated aliases are rebuilt on every run. Aliases that
an operator created by hand (``source="manual"``) are never touched.
"""

from __future__ import annotations

import csv
import zipfile
from dataclasses import dataclass
from pathlib import Path

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

# Suffix used for the type-specific alias ("abbevillecity", "autaugacounty").
TYPE_ALIAS_SUFFIX: dict[str, str] = {
    Place.PlaceType.CITY: "city",
    Place.PlaceType.TOWN: "town",
    Place.PlaceType.VILLAGE: "village",
    Place.PlaceType.CDP: "cdp",
    Place.PlaceType.COUNTY: "county",
    Place.PlaceType.COUNTY_SUBDIVISION: "township",
}


@dataclass(frozen=True)
class GazetteerDataset:
    """Describe one Gazetteer file and the places it contains."""

    stem: str
    count_key: str
    key_prefix: str
    place_type: str | None
    suffix: str | None


DATASETS: tuple[GazetteerDataset, ...] = (
    GazetteerDataset("state", "state", "us:state:", Place.PlaceType.STATE, None),
    GazetteerDataset(
        "counties", "county", "us:county:", Place.PlaceType.COUNTY, "county"
    ),
    GazetteerDataset(
        "cousubs",
        "cousub",
        "us:cousub:",
        Place.PlaceType.COUNTY_SUBDIVISION,
        "township",
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


def load_gazetteer(source_dir: Path, *, year: int = 2026) -> dict[str, int]:
    """Load every Gazetteer TSV in ``source_dir`` and return row counts.

    Missing files are skipped, so tests and partial downloads can load only the
    datasets they contain.
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
            legal_name = fields.pop("legal_name")
            place = upsert_place(dataset.key_prefix + geoid, **fields)
            refresh_aliases(place, legal_name=legal_name)
            counts[dataset.count_key] += 1
    return counts


def upsert_place(canonical_key: str, **fields) -> Place:
    """Create or update a place by its namespaced canonical key."""
    place, _ = Place.objects.update_or_create(
        canonical_key=canonical_key, defaults=fields
    )
    return place


def build_aliases(place: Place, *, legal_name: str | None = None) -> list[PlaceAlias]:
    """Return unsaved Gazetteer aliases for ``place``.

    Includes the cleaned name, name plus state, name plus full state name, a
    type-specific key when one applies, and the uncleaned legal name as a
    ``variant`` alias.
    """
    aliases: dict[tuple[str, str], PlaceAlias] = {}

    def add(kind: str, value: str) -> None:
        key = normalize_key(value)
        if key and (key, kind) not in aliases:
            aliases[(key, kind)] = PlaceAlias(
                place=place, key=key, kind=kind, source=PlaceAlias.Source.GAZETTEER
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

    type_suffix = TYPE_ALIAS_SUFFIX.get(place.place_type)
    if type_suffix:
        kind = (
            PlaceAlias.Kind.NAME_COUNTY
            if place.place_type == Place.PlaceType.COUNTY
            else PlaceAlias.Kind.NAME_CITY
        )
        add(kind, f"{place.name} {type_suffix}")

    if legal_name and normalize_key(legal_name) != normalize_key(place.name):
        add(PlaceAlias.Kind.VARIANT, legal_name)

    return list(aliases.values())


def refresh_aliases(place: Place, *, legal_name: str | None = None) -> int:
    """Replace generated aliases for ``place`` and return the number created.

    Manual aliases survive. When ``legal_name`` is omitted, an existing
    generated ``variant`` alias is retained so a plain refresh of a loaded place
    keeps its uncleaned legal name.
    """
    generated_variants: list[str] = []
    if legal_name is None:
        generated_variants = list(
            place.aliases.filter(kind=PlaceAlias.Kind.VARIANT)
            .exclude(source=PlaceAlias.Source.MANUAL)
            .values_list("key", flat=True)
        )
    manual_pairs = set(
        place.aliases.filter(source=PlaceAlias.Source.MANUAL).values_list("key", "kind")
    )
    place.aliases.exclude(source=PlaceAlias.Source.MANUAL).delete()

    aliases = build_aliases(place, legal_name=legal_name)
    existing_pairs = {(alias.key, alias.kind) for alias in aliases}
    for key in generated_variants:
        pair = (key, PlaceAlias.Kind.VARIANT)
        if key and pair not in existing_pairs:
            aliases.append(
                PlaceAlias(
                    place=place,
                    key=key,
                    kind=PlaceAlias.Kind.VARIANT,
                    source=PlaceAlias.Source.GAZETTEER,
                )
            )
            existing_pairs.add(pair)

    aliases = [
        alias for alias in aliases if (alias.key, alias.kind) not in manual_pairs
    ]
    PlaceAlias.objects.bulk_create(aliases, batch_size=1000)
    return len(aliases)


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Read a Gazetteer TSV, supporting pipe or tab delimiters."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        header = ""
        for line in handle:
            if line.strip():
                header = line
                break
        if not header:
            return []
        delimiter = "|" if "|" in header else "\t"
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = []
        for raw in reader:
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
        place_type = LSAD_PLACE_TYPE.get(lsad, Place.PlaceType.CITY)
        suffix = LSAD_SUFFIX.get(lsad)
    else:
        place_type = dataset.place_type
        suffix = dataset.suffix

    state = row.get("USPS", "").upper()
    if not state:
        try:
            state = state_from_fips(geoid[:2])
        except KeyError:
            state = ""

    fields = {
        "geoid": geoid,
        "name": clean_name(legal_name, suffix=suffix),
        "place_type": place_type,
        "state": state,
        "country": "US",
        "latitude": _parse_float(row.get("INTPTLAT")),
        "longitude": _parse_float(row.get("INTPTLONG")),
        # Consumed by refresh_aliases and not persisted on Place.
        "legal_name": legal_name,
    }
    return geoid, fields


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
