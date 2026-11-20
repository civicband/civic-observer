"""Normalization helpers for CivicBand Watch place data.

Downloaded gazetteer files live in ``settings.WATCH_DATA_DIR``. The helper
functions here keep place names and state codes consistent across ingest,
matching, and display.
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path

import httpx
from django.conf import settings
from localflavor.us.us_states import USPS_CHOICES

# Two-digit US FIPS state codes for the 50 states, DC, and Puerto Rico.
STATE_FIPS: dict[str, str] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "72": "PR",
}

PROVINCE_NAMES: dict[str, str] = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

# localflavor exposes USPS choices, including DC and territories, as (code, name).
# Materialize names as plain strings so callers never receive a lazy proxy.
STATE_NAMES: dict[str, str] = {code: str(name) for code, name in USPS_CHOICES}

# Suffixes that are unambiguous on their own. ``city``/``town``/``village`` are
# not included: "Kansas City" is a name, not a city suffix.
_DEFAULT_SUFFIXES = ("school district", "township", "county", "cdp")
_DEFAULT_SUFFIX_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(s) for s in _DEFAULT_SUFFIXES) + r")$",
    re.IGNORECASE,
)


def normalize_key(value: str) -> str:
    """Return a comparison key: lower-case ASCII letters and digits only."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return "".join(
        char for char in without_marks.lower() if char.isascii() and char.isalnum()
    )


def state_from_fips(fips: str) -> str:
    """Return the USPS abbreviation for a two-digit state FIPS code."""
    return STATE_FIPS[str(fips).strip().zfill(2)]


def state_name(state: str) -> str:
    """Return the full name for a USPS state or Canadian province code."""
    code = state.strip().upper()
    if code in STATE_NAMES:
        return STATE_NAMES[code]
    return PROVINCE_NAMES[code]


def clean_name(value: str, *, suffix: str | None = None) -> str:
    """Collapse whitespace and strip a legal place-type suffix.

    Without ``suffix``, only unambiguous suffixes (county, township, school
    district, CDP) are removed, so names such as "Kansas City" are preserved.
    When ``suffix`` is provided, only that exact trailing suffix is removed.
    """
    name = " ".join(value.split())
    if suffix is None:
        return _strip_repeatedly(name, _DEFAULT_SUFFIX_RE)

    wanted = " ".join(suffix.split())
    if not wanted:
        return name
    matcher = re.compile(rf"\s+{re.escape(wanted)}$", re.IGNORECASE)
    return matcher.sub("", name).strip()


def _strip_repeatedly(name: str, pattern: re.Pattern[str]) -> str:
    while True:
        stripped = pattern.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped


def ensure_data_dir() -> Path:
    """Create and return the configured watch data directory."""
    path = Path(settings.WATCH_DATA_DIR)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def download(
    url: str,
    filename: str,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download ``url`` to the watch data directory, reusing cached files."""
    directory = ensure_data_dir()
    target = directory / filename
    if target.is_file() and target.stat().st_size > 0:
        return target

    owns_client = client is None
    if client is None:
        client = httpx.Client(follow_redirects=True, timeout=300.0)

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".part",
    )
    temporary = Path(temporary_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as handle:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    finally:
        if owns_client:
            client.close()

    return target
