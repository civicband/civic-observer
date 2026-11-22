"""Tests for country normalization and the repair migration."""

import importlib

import pytest
from django.apps import apps

from municipalities.countries import (
    CANADIAN_STATE_CODES,
    country_for_state,
    to_iso_code,
)
from municipalities.models import Muni


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CA", "CA"),
        ("ca", "CA"),
        ("CAN", "CA"),
        ("Canada", "CA"),
        ("  canada ", "CA"),
        ("US", "US"),
        ("usa", "US"),
        ("United States of America", "US"),
        ("Atlantis", ""),
        ("", ""),
        ("  ", ""),
        ("none", ""),
        ("null", ""),
        (None, ""),
    ],
)
def test_to_iso_code(value, expected):
    assert to_iso_code(value) == expected


def test_country_for_state_covers_every_province():
    assert len(CANADIAN_STATE_CODES) == 13
    for code in CANADIAN_STATE_CODES:
        assert country_for_state(code) == "CA"
        assert country_for_state(code.lower()) == "CA"
    assert country_for_state("CA") == ""
    assert country_for_state("NY") == ""
    assert country_for_state(None) == ""


@pytest.mark.django_db
def test_repair_migration_fixes_names_and_province_rows():
    repair = importlib.import_module(
        "municipalities.migrations.0006_repair_muni_countries"
    )
    named = Muni.objects.create(
        subdomain="calgary.ab.canada", name="Calgary", state="AB", country="US"
    )
    mistyped = Muni.objects.create(
        subdomain="toronto.on.canada", name="Toronto", state="ON", country="Canada"
    )
    # GA is also the ISO code for Gabon; a US state must not be read as one.
    untouched = Muni.objects.create(
        subdomain="bryan.ga", name="Bryan", state="GA", country="US"
    )
    us_labelled_ab = Muni.objects.create(
        subdomain="yorkton.sk", name="Yorkton", state="SK", country="US"
    )

    repair._repair(apps, None)

    named.refresh_from_db()
    mistyped.refresh_from_db()
    untouched.refresh_from_db()
    us_labelled_ab.refresh_from_db()
    assert str(named.country) == "CA"
    assert str(mistyped.country) == "CA"
    assert str(untouched.country) == "US"
    assert str(us_labelled_ab.country) == "CA"
