# CivicBand Watch — Milestone 2: Gazetteer and Subreddit Classifier

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load US and Canadian places from Census and GeoNames, mark government units, link civic-observer `Muni` rows to places, and classify every subreddit with a layered, reviewable classifier.

**Architecture:** `watch/gazetteer.py` owns downloads, normalization, and idempotent upserts. `watch/classify.py` is a pure layered classifier that reads `PlaceAlias` and `Subreddit` rows and returns a `Classification`. Management commands wrap each loader so operators can refresh data without a deploy. Every classification records `source`, `confidence`, and `reviewed_at`.

**Tech Stack:** Django 5.2, PostgreSQL 17, `openpyxl`, `httpx`, `difflib`, `localflavor`, pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- Every US place resolves to a namespaced `canonical_key` and, where available, a Census GEOID. Never create a `Place` from free text.
- `Place.geoid` is nullable and unique only with `place_type`. Never use a bare GEOID as a primary key.
- The raw `Govt_Units_2022_Final.xlsx` and downloaded Gazetteer/GeoNames files are not committed. The loader caches them under `WATCH_DATA_DIR` and accepts `--source-dir`.
- Tests never hit the network. Use small hand-made TSV/CSV/dict fixtures.
- Manual subreddit overrides always win. A classifier run must never overwrite a `Subreddit` row with `source="manual"`.
- US-only data model. Canadian municipalities are `Place` rows with `place_type="canadian_city"` and `country="CA"`. Other countries are country rows and `NonUsCity` lookup rows.
- No migrations against `meetings` or `municipalities`. This milestone adds `watch` tables only; `Place.legal_name` is the only column added to an existing watch table.
- Run `uv run pytest`, `ruff`, and `mypy` before each commit.

## Review Focus

- Running any loader twice must leave row counts and place links unchanged.
- A place and a school district sharing a 7-digit GEOID must remain two distinct `Place` rows.
- A county-only manual label (`chestercounty`) must resolve to the county or stay in review; it must never resolve to a city named Chester.
- A blank-state manual label (`glendale`) must not silently pick Glendale CA or AZ.
- A manual mute or note set by an operator must survive re-seeding `subreddits.csv`.

---

## Data Sources and Keys

| Source | URL or input | Key |
|---|---|---|
| Census Gazetteer states | `https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_state_national.zip` | `us:state:<GEOID>` |
| Census Gazetteer counties | `.../2026_Gaz_counties_national.zip` | `us:county:<GEOID>` |
| Census Gazetteer county subdivisions | `.../2026_Gaz_cousubs_national.zip` | `us:cousub:<GEOID>` |
| Census Gazetteer places | `.../2026_Gaz_place_national.zip` | `us:place:<GEOID>` |
| Census Gazetteer school districts | `.../2026_Gaz_elsd_national.zip`, `..._scsd_national.zip`, `..._unsd_national.zip` | `us:school:<GEOID>` |
| Census of Governments | `https://www2.census.gov/programs-surveys/gus/datasets/2022/govt_units_2022.ZIP` | match by FIPS |
| GeoNames cities | `https://download.geonames.org/export/dump/cities500.zip` | `ca:city:<geonameid>` for Canada; `NonUsCity` for others |
| Wikidata | SPARQL endpoint, optional | `Subreddit` rows with `source="wikidata"` |

The Gazetteer TSV files use `|` or tab delimiters depending on vintage. The 2026 files are pipe-delimited with a header. Read with `csv.DictReader(delimiter="|")`; strip whitespace from keys and values.

GNIS is not loaded in this milestone. The Gazetteer names plus GeoNames alternate names cover the classifier’s needs; GNIS can be added later as a cross-check without changing the model.

---

### Task 1: Data directory, normalization, and CSV/TSV helpers

**Files:**
- Create: `watch/normalize.py`
- Create: `watch/data/__init__.py`
- Modify: `config/settings/base.py` (already has `WATCH_DATA_DIR` in milestone 1)
- Modify: `.gitignore`
- Test: `tests/watch/test_normalize.py`

**Interfaces:**
- Produces:
  - `normalize_key(value: str) -> str`: lower-case, Unicode NFKD, strip combining marks, keep ASCII letters and digits, remove all else.
  - `state_from_fips(fips: str) -> str`: two-digit FIPS to USPS abbreviation; raise `KeyError` for unknown.
  - `state_name(state: str) -> str`: USPS abbreviation to full name using `localflavor.us.us_states.STATES`; for Canadian province codes (AB, BC, MB, NB, NL, NS, NT, NU, ON, PE, QC, SK, YT) return the province name from a small `PROVINCE_NAMES` map.
  - `clean_name(value: str, *, suffix: str | None = None) -> str`: collapse whitespace. With `suffix`, strip only that exact trailing legal suffix (case-insensitive). Without `suffix`, strip only unambiguous suffixes `county`, `township`, `school district`, `CDP`; never strip `city`, `town`, or `village` from a proper name such as Kansas City. `Place.name` uses the LSAD-specific suffix; aliases also include the uncleaned legal name.
  - `ensure_data_dir() -> Path`: create `settings.WATCH_DATA_DIR` and return it.
  - `download(url: str, filename: str, *, client: httpx.Client | None = None) -> Path`: reuse an existing non-empty file, otherwise download to `WATCH_DATA_DIR/filename` and return the path.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from watch.normalize import clean_name, normalize_key, state_from_fips, state_name


def test_normalize_key_strips_punctuation_and_case():
    assert normalize_key("La Crosse, WI") == "lacrossewi"
    assert normalize_key("Fountain–Colorado") == "fountaincolorado"


def test_state_helpers():
    assert state_from_fips("06") == "CA"
    assert state_name("CA") == "California"


def test_clean_name_removes_only_explicit_or_unambiguous_suffixes():
    assert clean_name("Kansas City") == "Kansas City"
    assert clean_name("Abbeville city") == "Abbeville city"
    assert clean_name("Abbeville city", suffix="city") == "Abbeville"
    assert clean_name("Cecil County") == "Cecil"
    assert clean_name("East Windsor town", suffix="town") == "East Windsor"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_normalize.py -q --no-cov`
Expected: FAIL because `watch.normalize` does not exist.

- [ ] **Step 3: Implement normalization and download helpers**

Include a `STATE_FIPS` dict for the 50 states, DC, and PR. Add `.cache/` to `.gitignore` if absent.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_normalize.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/normalize.py watch/data .gitignore tests/watch/test_normalize.py
git commit -m "feat(watch): add place normalization helpers"
```

---

### Task 2: Census Gazetteer loader and alias generation

**Files:**
- Create: `watch/gazetteer.py`
- Create: `watch/management/commands/load_watch_gazetteer.py`
- Test: `tests/watch/test_gazetteer.py`
- Create fixtures: `tests/watch/fixtures/gazetteer/2026_Gaz_place_national.txt`, `..._counties_national.txt`, `..._cousubs_national.txt`, `..._elsd_national.txt`, `..._state_national.txt`

**Interfaces:**
- Consumes: `normalize_key`, `clean_name`, `state_from_fips`, `ensure_data_dir`, `download`; models from milestone 1.
- Produces:
  - `load_gazetteer(source_dir: Path, *, year: int = 2026) -> dict[str, int]` returning counts keyed `state`, `county`, `cousub`, `place`, `school`.
  - `upsert_place(canonical_key, **fields) -> Place` using `update_or_create`.
  - `build_aliases(place: Place) -> list[PlaceAlias]` returning unsaved aliases with kinds `name`, `name_state`, `name_state_name`, `name_city`, `name_county`, plus `variant` when `Place.legal_name` differs from `Place.name`.
  - `refresh_aliases(place: Place) -> int` replacing only `source="gazetteer"` aliases while preserving `manual`, `geonames`, and `wikidata` aliases.
  - Adds one nullable `Place.legal_name` field through `watch/migrations/0002_place_legal_name.py`; the loader always sets it to the official Gazetteer `NAME`. For the `place` dataset, `Place.name=clean_name(NAME, suffix=LSAD_SUFFIX[LSAD])`; for `county` and `cousub`, `Place.name=NAME` (so `Carson City` and `Baltimore city` survive) and the suffix-stripped form is alias-only.
  - Command options: `--download`, `--source-dir PATH`, `--year 2026`, `--dry-run`.

- [ ] **Step 1: Write the failing loader tests**

Use a small TSV fixture with two states, two counties, two places (one city and one CDP with the same name in different states), one county subdivision, and one school district. Assert:

```python
@pytest.mark.django_db
def test_loader_creates_typed_places_and_is_idempotent(gazetteer_dir):
    first = load_gazetteer(gazetteer_dir)
    second = load_gazetteer(gazetteer_dir)
    assert first == second
    assert (
        Place.objects.count()
        == first["state"]
        + first["county"]
        + first["cousub"]
        + first["place"]
        + first["school"]
    )
    assert (
        Place.objects.get(geoid="0600001", place_type="city").canonical_key
        == "us:place:0600001"
    )


@pytest.mark.django_db
def test_same_geoid_different_types_are_distinct(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    assert Place.objects.filter(geoid="0600001").count() == 2


@pytest.mark.django_db
def test_aliases_include_state_and_type_keys(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    keys = set(
        PlaceAlias.objects.filter(place__name="Abbeville").values_list("key", flat=True)
    )
    assert {"abbeville", "abbevilleal", "abbevillealabama", "abbevillecity"} <= keys
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_gazetteer.py -q --no-cov`
Expected: FAIL because the loader does not exist.

- [ ] **Step 3: Implement the loader**

For each file:

- Read the TSV, skip blank lines, and use `GEOID`, `NAME`, `LSAD`, `INTPTLAT`, `INTPTLONG`, and `USPS` when present.
- Map `place_type` from `LSAD`: `25` → `city`, `43`/`153` → `town`, `47` → `village`, `57` → `cdp`, `21`/`53` → `city`; default `city`.
- Map county subdivisions to `county_subdivision`; school files to `school_district`; state files to `state`.
- Always set `Place.legal_name=NAME`. For the `place` dataset, set `Place.name=clean_name(NAME, suffix=LSAD_SUFFIX[LSAD])`; `LSAD_SUFFIX` maps 25→`city`, 43/153→`town`, 47→`village`, 57→`CDP`. For `county` and `cousub`, set `Place.name=NAME` and generate suffix-stripped aliases from `KNOWN_LEGAL_SUFFIXES` (`county`, `parish`, `borough`, `census area`, `municipality`, `city`, `town`, `township`, `village`, `CDP`, `CCD`, `UT`, `city and borough`, `city and county`, `unified government`). Never strip for county/cousub display names.
- Set `country="US"`, `state` from `USPS` or the first two GEOID digits via `state_from_fips`.
- Parse `INTPTLAT`/`INTPTLONG` into floats when present.
- Call `refresh_aliases`.

Command: `--download` fetches the six Gazetteer zips into `WATCH_DATA_DIR` and extracts them; `--source-dir` reads existing files. Use `zipfile` stdlib for extraction.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_gazetteer.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/gazetteer.py watch/management/commands/load_watch_gazetteer.py tests/watch/test_gazetteer.py tests/watch/fixtures/gazetteer
git commit -m "feat(watch): load census gazetteer places and aliases"
```

---

### Task 3: Census of Governments loader

**Files:**
- Modify: `watch/gazetteer.py`
- Create: `watch/management/commands/load_watch_governments.py`
- Test: `tests/watch/test_governments.py`
- Create fixture: `tests/watch/fixtures/governments.csv`

**Interfaces:**
- Produces:
  - `load_government_rows(rows: Iterable[dict]) -> dict[str, int]` accepting already-normalized dict rows with keys `unit_type`, `fips_state`, `fips_county`, `fips_place`, `unit_name`, `population`, `is_active`.
  - `load_governments(xlsx_path: Path) -> dict[str, int]` opening the XLSX with `openpyxl.load_workbook(..., read_only=True, data_only=True)`, reading the `General Purpose` and `School District` sheets, and yielding dict rows.
  - `government_key(unit_type: str, state: str, county: str, place: str) -> str | None` returning `us:county:...`, `us:place:...`, or `us:cousub:...`.
- Matching rules:
  - `1 - COUNTY`: `f"us:county:{state}{county}"`.
  - `2 - MUNICIPAL`: `f"us:place:{state}{place}"`.
  - `3 - TOWNSHIP`: `f"us:cousub:{state}{county}{place}"`.
  - School districts: match `Place` where `place_type="school_district"`, `state=...`, and `normalize_key(clean_name(unit_name, suffix="school district"))` equals `normalize_key(clean_name(place.name, suffix="school district"))`. If zero or multiple match, count as `unmatched_school`.
- On match: set `is_government=True`, `population` when positive, and `county` to the matching county `Place` when `place_type != "county"`. Skip `is_active` values other than `Y`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_government_rows_mark_counties_municipalities_townships(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "1 - COUNTY",
                "fips_state": "06",
                "fips_county": "001",
                "fips_place": "99001",
                "unit_name": "COUNTY OF ALAMEDA",
                "population": "1629540",
                "is_active": "Y",
            },
            {
                "unit_type": "2 - MUNICIPAL",
                "fips_state": "06",
                "fips_county": "001",
                "fips_place": "00001",
                "unit_name": "CITY OF ALBANY",
                "population": "20271",
                "is_active": "Y",
            },
            {
                "unit_type": "3 - TOWNSHIP",
                "fips_state": "09",
                "fips_county": "110",
                "fips_place": "24800",
                "unit_name": "TOWN OF EAST WINDSOR",
                "population": "12345",
                "is_active": "Y",
            },
        ]
    )
    assert stats == {
        "county": 1,
        "municipal": 1,
        "township": 1,
        "school": 0,
        "unmatched_school": 0,
        "skipped": 0,
    }
    assert Place.objects.get(canonical_key="us:county:06001").is_government is True
    assert Place.objects.get(canonical_key="us:place:0600001").population == 20271
    assert Place.objects.get(canonical_key="us:place:0600001").county.name == "Alameda"


@pytest.mark.django_db
def test_inactive_units_are_skipped(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "2 - MUNICIPAL",
                "is_active": "N",
                "fips_state": "06",
                "fips_place": "00001",
            }
        ]
    )
    assert stats["skipped"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_governments.py -q --no-cov`
Expected: FAIL because `load_government_rows` does not exist.

- [ ] **Step 3: Implement the loader and command**

Use a helper `_cell(row, name)` that tolerates blank trailing cells. Log unmatched school districts with a count, not per-row noise. The command accepts `--source-dir PATH` containing `Govt_Units_2022_Final.xlsx` or `--file PATH`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_governments.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/gazetteer.py watch/management/commands/load_watch_governments.py tests/watch/test_governments.py tests/watch/fixtures/governments.csv
git commit -m "feat(watch): load census government unit flags and populations"
```

---

### Task 4: GeoNames loader, Canadian places, and non-US lookup

**Files:**
- Modify: `watch/models.py` (add `NonUsCity` and `NonUsCityAlias`), `watch/migrations/0003_nonuscity.py`
- Modify: `watch/gazetteer.py`
- Create: `watch/management/commands/load_watch_geonames.py`
- Test: `tests/watch/test_geonames.py`
- Create fixture: `tests/watch/fixtures/cities500_sample.txt`

**Interfaces:**
- Produces:
  - `NonUsCity`: bigint pk; `geoname_id` bigint unique; `name` char(255); `normalized_name` char(255) indexed; `country` country field; `admin1` char(10) blank; `population` positive int default 0.
  - `NonUsCityAlias`: bigint pk; `city` FK CASCADE related `aliases`; `key` char(255) indexed; unique `(city, key)`.
  - `load_geonames(cities_path: Path) -> dict[str, int]` returning `{"canadian_places": ..., "non_us_cities": ..., "aliases": ..., "skipped": ...}`.
  - Canadian rows create `Place(canonical_key=f"ca:city:{geonameid}", place_type="canadian_city", country="CA", state=admin1, population=...)` plus aliases.
  - Non-Canadian rows with population >= 15000 create `NonUsCity` plus aliases for `name` and every non-empty `alternatenames` entry.
  - US rows are not used for place creation, but when a US GeoNames row matches a `Place` by normalized name + state and `Place.county` is null, set `Place.county` from `admin2` when it maps to a loaded county. This is the best-effort source for CDP counties; leave null when ambiguous.
- Command options: `--source-dir`, `--download`, `--min-population 15000`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_canadian_city_becomes_place_and_non_us_city_is_lookup(cities500_file):
    stats = load_geonames(cities500_file)
    assert (
        Place.objects.get(canonical_key="ca:city:6094817").place_type == "canadian_city"
    )
    assert NonUsCity.objects.get(geoname_id=2643743).country == "GB"
    assert NonUsCityAlias.objects.filter(key="london").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_geonames.py -q --no-cov`
Expected: FAIL because the loader/models do not exist.

- [ ] **Step 3: Implement the models, loader, and command**

Parse `cities500.txt` as tab-separated, 19 columns. Use `feature class == "P"`. Skip `country code == "US"` for `NonUsCity` (US aliases are already covered by the Gazetteer and manual aliases). For Canada, create both a `Place` and a `NonUsCity` row so mute-by-country still works.

Generate the migration:

```bash
uv run python manage.py makemigrations watch
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_geonames.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/models.py watch/migrations watch/gazetteer.py watch/management/commands/load_watch_geonames.py tests/watch/test_geonames.py tests/watch/fixtures/cities500_sample.txt
git commit -m "feat(watch): load geonames canadian places and non-us cities"
```

---

### Task 5: Seed subreddits and manual aliases

**Files:**
- Create: `watch/data/subreddits.csv` (copy of `_civic_band_watch/subreddits.csv`)
- Create: `watch/data/aliases.csv` with at least `key,place,state` rows including `slc,Salt Lake City,UT`
- Create: `watch/management/commands/seed_watch_subreddits.py`
- Create: `watch/subreddits.py`
- Test: `tests/watch/test_seed_subreddits.py`

**Interfaces:**
- Produces:
  - `seed_subreddits(csv_path: Path) -> dict[str, int]` returning `{"created": ..., "updated": ..., "unresolved": ...}`.
  - `seed_aliases(csv_path: Path) -> int`.
  - `resolve_manual_place(name: str | None, state: str | None, county: str | None) -> Place | None`.
  - `infer_country(note: str) -> str | None` mapping `Ireland`→`IE`, `UK`/`United Kingdom`→`GB`, `Australia`→`AU`, `New Zealand`→`NZ`, `Canada`→`CA`; otherwise `None`.
- Seeding rules:
  - Lowercase the subreddit name.
  - Set `source="manual"`, `reviewed_at=timezone.now()`.
  - Never overwrite an existing `muted=True` or a non-blank `note` with a blank CSV value.
  - For a row with `place`, resolve by name+state; if state is blank and multiple places match, leave `place=None` and keep the note.
  - For a row with only `county`, resolve to a `Place` with `place_type="county"` and matching state; never a city.
  - Preserve `kind`, `state`, `country`, `confidence`, and `note`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_seed_subreddits_resolves_places_and_counties(subreddit_places):
    stats = seed_subreddits(Path("watch/data/subreddits.csv"))
    assert stats["unresolved"] >= 0
    assert Subreddit.objects.get(name="chicoca").place.name == "Chico"
    assert Subreddit.objects.get(name="cecilcounty").place.place_type == "county"
    assert Subreddit.objects.get(name="glendale").place is None
    assert Subreddit.objects.get(name="kingstonontario").kind == "non_us"


@pytest.mark.django_db
def test_seed_is_idempotent_and_preserves_mute(subreddit_places):
    seed_subreddits(Path("watch/data/subreddits.csv"))
    Subreddit.objects.filter(name="lol").update(muted=True, note="memes")
    seed_subreddits(Path("watch/data/subreddits.csv"))
    row = Subreddit.objects.get(name="lol")
    assert row.muted is True
    assert row.note == "memes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_seed_subreddits.py -q --no-cov`
Expected: FAIL because the seed service does not exist.

- [ ] **Step 3: Implement the seed service and command**

Copy the CSV verbatim from the supplied directory. Do not include any private data; subreddit names are public. Add `slc` and any other manual aliases needed by the 63-label test. Use `update_or_create(name=..., defaults=...)` but merge `muted` and `note` explicitly.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_seed_subreddits.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/data watch/subreddits.py watch/management/commands/seed_watch_subreddits.py tests/watch/test_seed_subreddits.py
git commit -m "feat(watch): seed subreddit labels and manual aliases"
```

---

### Task 6: Wikidata subreddit links

**Files:**
- Create: `watch/wikidata.py`
- Create: `watch/management/commands/load_watch_wikidata.py`
- Test: `tests/watch/test_wikidata.py`
- Create fixture: `tests/watch/fixtures/wikidata_bindings.json`

**Interfaces:**
- Produces:
  - `SPARQL_QUERY` selecting items with property `P3984` (subreddit), optional `P1566` (GeoNames ID), item label, and the label of `P131` (located in administrative territorial entity).
  - `parse_wikidata_bindings(payload: dict) -> list[dict]` returning `{"subreddit": ..., "place_name": ..., "state": ..., "country": ...}` rows.
  - `load_wikidata(*, endpoint: str = "https://query.wikidata.org/sparql", fetch=None) -> dict[str, int]` returning `{"linked": ..., "unmatched": ..., "skipped": ...}`.
- Rules:
  - Normalize subreddit `r/foo` to `foo`.
  - Match `place_name` against `PlaceAlias`/`Place.name`; require an exact normalized name. If a state or country is present, use it to disambiguate. Convert a full state label such as “North Carolina” to `NC` through the same `STATE_FIPS`/`state_name` helpers; ignore a state label that does not map.
  - Create or update a `Subreddit` row with `source="wikidata"`, `kind="place"`, `place=matched`, `confidence="high"`, `reviewed_at=None`.
  - Never overwrite `source="manual"` rows. Count them as `skipped`.
  - If multiple places match or none match, count `unmatched` and do not write a wrong place.
- Command options: `--endpoint`, `--file PATH`, `--dry-run`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_wikidata_links_subreddit_to_place(wikidata_places):
    payload = {
        "results": {
            "bindings": [
                {
                    "subreddit": {"value": "bullcity"},
                    "placeLabel": {"value": "Durham"},
                    "stateLabel": {"value": "North Carolina"},
                }
            ]
        }
    }
    stats = load_wikidata(fetch=lambda endpoint: payload)
    assert stats["linked"] == 1
    row = Subreddit.objects.get(name="bullcity")
    assert row.place.name == "Durham"
    assert row.source == "wikidata"


@pytest.mark.django_db
def test_wikidata_does_not_overwrite_manual(wikidata_places):
    Subreddit.objects.create(
        name="bullcity", kind="general", source="manual", confidence="high"
    )
    payload = {
        "results": {
            "bindings": [
                {"subreddit": {"value": "bullcity"}, "placeLabel": {"value": "Durham"}}
            ]
        }
    }
    stats = load_wikidata(fetch=lambda endpoint: payload)
    assert stats["skipped"] == 1
    assert Subreddit.objects.get(name="bullcity").kind == "general"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_wikidata.py -q --no-cov`
Expected: FAIL because `watch.wikidata` does not exist.

- [ ] **Step 3: Implement the parser, matcher, and command**

Use `httpx.get(endpoint, params={"query": SPARQL_QUERY, "format": "json"}, headers={"User-Agent": "civicband-watch/0.1"}, timeout=60)`. Keep the query text in the module so it can be reviewed and tested. `--file` reads a saved JSON response for offline use.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_wikidata.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/wikidata.py watch/management/commands/load_watch_wikidata.py tests/watch/test_wikidata.py tests/watch/fixtures/wikidata_bindings.json
git commit -m "feat(watch): load wikidata subreddit place links"
```

---

### Task 7: Link `Muni` rows to places

**Files:**
- Create: `watch/muni_link.py`
- Create: `watch/management/commands/link_watch_munis.py`
- Test: `tests/watch/test_muni_link.py`

**Interfaces:**
- Produces:
  - `link_munis(*, dry_run: bool = False) -> dict[str, int]` returning `{"linked": ..., "ambiguous": ..., "no_match": ..., "already": ...}`.
  - `match_muni(muni) -> tuple[Place | None, str]` returning `(place, status)` where status is `auto`, `no_match`, or `ambiguous`.
- Rules:
  - Exact key `normalize_key(f"{muni.name}{muni.state}")` against `PlaceAlias` first.
  - Fall back to exact `Place.name` + `state`.
  - If multiple, choose the closest by latitude/longitude when both Muni and Place have coordinates and the distance is under 0.25 degrees; otherwise `ambiguous`.
  - Set `Place.muni`, `Place.muni_link_status="auto"`, `Place.muni_reviewed_at=timezone.now()`.
  - Never write to `municipalities.Muni`.
  - Honor `muni_link_status="confirmed"`: skip it.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_link_muni_by_name_and_state():
    muni = MuniFactory(name="Chico", state="CA")
    place = PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")
    stats = link_munis()
    place.refresh_from_db()
    assert stats["linked"] == 1
    assert place.muni == muni
    assert place.muni_link_status == "auto"


@pytest.mark.django_db
def test_link_leaves_ambiguous_muni_in_review():
    muni = MuniFactory(name="Glendale", state="CA")
    PlaceFactory(name="Glendale", state="CA", canonical_key="us:place:0630000")
    PlaceFactory(name="Glendale", state="CA", canonical_key="us:place:0699999")
    stats = link_munis()
    assert stats["linked"] == 0
    assert Place.objects.filter(muni=muni).count() == 0


@pytest.mark.django_db
def test_same_name_in_another_state_is_no_match():
    muni = MuniFactory(name="Chico", state="CA")
    PlaceFactory(name="Chico", state="WA", canonical_key="us:place:5300000")
    stats = link_munis()
    assert stats["no_match"] == 1
    assert Place.objects.filter(muni=muni).count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_muni_link.py -q --no-cov`
Expected: FAIL because `watch.muni_link` does not exist.

- [ ] **Step 3: Implement the matcher and command**

Use `math.dist` or a squared-degree distance for the coordinate tie-break. The command options are `--dry-run` and `--limit`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_muni_link.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/muni_link.py watch/management/commands/link_watch_munis.py tests/watch/test_muni_link.py
git commit -m "feat(watch): link municipalities to gazetteer places"
```

---

### Task 8: Layered subreddit classifier

**Files:**
- Create: `watch/classify.py`
- Create: `watch/management/commands/classify_watch_subreddits.py`
- Test: `tests/watch/test_classifier.py`
- Create fixture: `tests/watch/fixtures/classifier_places.py` or a pytest fixture module

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Classification: kind: str; place: Place | None; state: str; country: str | None; confidence: str; source: str; note: str = ""`
  - `classify_subreddit(name: str, *, use_manual: bool = True) -> Classification`
  - `apply_classification(subreddit: Subreddit, classification: Classification) -> bool` returning whether it changed. It writes only `kind`, `place`, `state`, `country`, `confidence`, `source`, `reviewed_at`, and `note`; it never changes `muted` or an existing non-blank note.
  - `classify_pending_subreddits(*, reclassify: bool = False) -> dict[str, int]`
- Layer order, first match wins:
  1. Manual `Subreddit` row with `source="manual"` when `use_manual=True`.
  2. `Subreddit` row with `source="wikidata"`.
  3. Strong non-place guard, required to satisfy the 63-label no-wrong-place constraint:
     - exact raw state or region key (full state names only, not bare abbreviations);
     - a `NonUsCityAlias` with population >= 15,000 when the best US alias candidate is absent or has population < non-US population / 10. This catches Brisbane AU vs Brisbane CA and Galway IE vs Galway NY.
  4. Exact `PlaceAlias.key` match; choose by population when unambiguous, otherwise return `kind="ambiguous"`.
  5. Affix stripping with prefixes/suffixes `development`, `daily`, `news`, `talk`, `landlords`, `ask`, `r4r`, `jobs`; retry at `confidence="low"`. In this layer, check state/region keys before `PlaceAlias` so `oregontalk` returns state OR.
  6. Ambiguity rule: choose the most populous only when it is at least 10× the next; otherwise `kind="ambiguous"` and `place=None`.
  7. State and region names. `REGIONS` is a module constant: `{"bayarea": ("CA", "Bay Area"), "socal": ("CA", "Southern California"), "pnw": (None, "Pacific Northwest"), "pnwdaily": (None, "Pacific Northwest"), "dmv": (None, "DC/Maryland/Virginia")}`. State rows set `kind="state"` and `state`; region rows set `kind="region"` and `state` when known.
  8. `NonUsCityAlias` match; if the city is Canadian and a `Place` with `country="CA"` matches, return `kind="place"`, otherwise `kind="non_us"` with `country`.
  9. `kind="general"`, `confidence="low"`, `source="gazetteer"`.

- [ ] **Step 1: Write the failing classifier tests**

Use a fixture that creates the places needed for the 63-label set (Chico CA, Los Angeles CA, Salt Lake City UT, La Crosse WI, Fountain CO, Cecil County MD, Chester County PA, Glendale CA and AZ with populations, Seattle WA, Durham NC, McKinney TX, and the non-US cities London/Dublin/Belfast/Auckland). The fixture returns a dict mapping each subreddit name to the expected `Place` (or `None` for ambiguous/general rows). Add a module-level helper:

```python
import csv


def read_subreddits_csv(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))
```

Then:

```python
@pytest.mark.django_db
def test_classifier_reproduces_hand_labels_without_wrong_places(classifier_places):
    wrong = []
    for row in read_subreddits_csv(Path("watch/data/subreddits.csv")):
        result = classify_subreddit(row["subreddit"], use_manual=False)
        expected = classifier_places[row["subreddit"]]
        if row["kind"] == "general":
            ok = result.kind == "general"
        elif row["kind"] == "state":
            ok = result.kind == "state" and result.state == row["state"]
        elif row["kind"] == "region":
            ok = result.kind == "region"
        elif row["kind"] == "non_us":
            ok = result.kind == "non_us" and not result.place
        else:
            ok = result.kind == "ambiguous" or (
                result.kind == "place" and result.place == expected
            )
        if not ok:
            wrong.append(
                (
                    row["subreddit"],
                    result.kind,
                    getattr(result.place, "name", None),
                    expected,
                )
            )
    assert wrong == []


@pytest.mark.django_db
def test_development_slc_uses_alias(classifier_places):
    result = classify_subreddit("developmentslc", use_manual=False)
    assert result.place.name == "Salt Lake City"


@pytest.mark.django_db
def test_glendale_is_ambiguous(classifier_places):
    result = classify_subreddit("glendale", use_manual=False)
    assert result.kind == "ambiguous"
    assert result.place is None


@pytest.mark.django_db
def test_manual_override_wins(classifier_places):
    Subreddit.objects.create(
        name="chicoca", kind="general", source="manual", confidence="high"
    )
    result = classify_subreddit("chicoca", use_manual=True)
    assert result.kind == "general"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_classifier.py -q --no-cov`
Expected: FAIL because `watch.classify` does not exist.

- [ ] **Step 3: Implement the classifier**

Load candidate places for a key with `PlaceAlias.objects.filter(key=key).select_related("place")`. Cache state and region lookups at module level as plain tuples. Do not call the database for every state name.

Command `classify_watch_subreddits` supports `--reclassify` to re-run non-manual rows and `--limit`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_classifier.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/classify.py watch/management/commands/classify_watch_subreddits.py tests/watch/test_classifier.py tests/watch/fixtures
git commit -m "feat(watch): classify subreddits through gazetteer layers"
```

---

### Task 9: Review admin and milestone verification

**Files:**
- Modify: `watch/admin.py`
- Test: `tests/watch/test_admin_review.py`

**Interfaces:**
- Produces:
  - `SubredditAdmin` with `list_filter = ["kind", "source", "muted", "reviewed_at"]`, `search_fields = ["name", "place__name", "note"]`, autocomplete for `place`, and an action `accept_suggestions` setting `reviewed_at=timezone.now()`.
  - `PlaceAdmin` with autocomplete for `county`, `muni`; filters `place_type`, `state`, `is_government`, `muni_link_status`; search `name`, `geoid`, `canonical_key`.
  - A `Needs review` filter on `Subreddit` for `reviewed_at__isnull=True`.
  - A `Needs review` filter on `Place` for `muni_link_status__in=["unlinked", "no_match"]`.

- [ ] **Step 1: Write the failing admin tests**

```python
@pytest.mark.django_db
def test_subreddit_admin_accept_suggestions(admin_client):
    row = Subreddit.objects.create(
        name="chicoca", kind="place", source="gazetteer", confidence="high"
    )
    admin_client.post(
        reverse("admin:watch_subreddit_changelist"),
        {"action": "accept_suggestions", "_selected_action": [row.pk]},
    )
    row.refresh_from_db()
    assert row.reviewed_at is not None


@pytest.mark.django_db
def test_place_admin_filters_unlinked(admin_client):
    response = admin_client.get(
        reverse("admin:watch_place_changelist"),
        {"muni_link_status__in": "unlinked,no_match"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_admin_review.py -q --no-cov`
Expected: FAIL because the admin additions do not exist.

- [ ] **Step 3: Implement the admin changes**

Use `autocomplete_fields` and ensure related admins define `search_fields`. Add the custom `SimpleListFilter` classes.

- [ ] **Step 4: Run the full watch suite and repo checks**

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

- [ ] **Step 5: Commit**

```bash
git add watch/admin.py tests/watch/test_admin_review.py
git commit -m "feat(watch): add gazetteer and subreddit review admin"
```

---

## Milestone 2 Acceptance

- `load_watch_gazetteer` is idempotent and creates typed places with namespaced keys.
- The same GEOID can exist as a place and a school district without a constraint error.
- `load_watch_governments` marks counties, municipalities, and townships as governments and fills population/county where available.
- `load_watch_geonames` creates Canadian `Place` rows and non-US `NonUsCity` lookup rows.
- `load_watch_wikidata` links exact subreddit-to-place rows without overwriting manual labels.
- `seed_watch_subreddits` resolves the 63 labels, preserves operator mutes/notes, and leaves ambiguous labels for review.
- Every `Muni` is linked or appears in the review filter.
- With manual overrides off, the classifier assigns no wrong place to any of the 63 hand-labelled subreddits; ambiguous is allowed.
- `uv run pytest -q`, ruff, and mypy pass.
