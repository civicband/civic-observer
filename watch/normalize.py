"""Normalization helpers for CivicBand Watch place data.

Downloaded gazetteer files live in ``settings.WATCH_DATA_DIR``. The helper
functions here keep place names and state codes consistent across ingest,
matching, and display.
"""

from __future__ import annotations

import re
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
STATE_NAMES: dict[str, str] = dict(USPS_CHOICES)

_LEGAL_SUFFIXES = (
    "school district",
    "township",
    "village",
    "county",
    "city",
    "town",
    "cdp",
)
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(s) for s in _LEGAL_SUFFIXES) + r")$",
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
    return STATE_FIPS[fips]


def state_name(state: str) -> str:
    """Return the full name for a USPS state or Canadian province code."""
    code = state.strip().upper()
    if code in STATE_NAMES:
        return STATE_NAMES[code]
    return PROVINCE_NAMES[code]


def clean_name(value: str) -> str:
    """Collapse whitespace and strip trailing legal place-type suffixes."""
    name = " ".join(value.split())
    while True:
        stripped = _LEGAL_SUFFIX_RE.sub("", name).strip()
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
    temporary = target.with_name(f"{target.name}.part")
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
        if owns_client:
            client.close()

    return target
