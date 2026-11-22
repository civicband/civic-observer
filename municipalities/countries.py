"""Country helpers for municipality records.

``sites.country`` in the clerk database is a free-text ``VARCHAR`` that the
deploy pipeline forwards verbatim, so CivicObserver receives values such as
``"Canada"`` as often as ``"CA"``.  ``Muni.country`` is a
``django_countries.CountryField`` whose stored value is treated as an ISO
3166-1 alpha-2 code elsewhere, and that field does not normalize a name to a
code on write.  Everything that accepts a country must go through
:func:`to_iso_code` first.
"""

from __future__ import annotations

from django_countries import countries

#: Canadian province and territory codes accepted by ``Muni.state``.
CANADIAN_STATE_CODES: frozenset[str] = frozenset(
    {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
)

#: Country implied by each Canadian province code.
CANADIAN_COUNTRY = "CA"


def to_iso_code(value: object) -> str:
    """Return the ISO 3166-1 alpha-2 code for ``value``, or ``""``.

    Values are whitespace-collapsed and matched case-insensitively. Both
    codes (``"ca"``, ``"CAN"``) and English names (``"Canada"``,
    ``"CANADA"``) resolve; anything else, including an empty value, returns
    ``""`` so the caller can fall back rather than store a value the rest of
    the app cannot read.
    """
    code = getattr(value, "code", None)
    text = " ".join(str(code if code is not None else (value or "")).split())
    if not text or text.lower() in {"none", "null"}:
        return ""
    return countries.alpha2(text) or countries.by_name(text)


def country_for_state(state: object) -> str:
    """Return the country implied by a state/province code, or ``""``."""
    if str(state or "").strip().upper() in CANADIAN_STATE_CODES:
        return CANADIAN_COUNTRY
    return ""
