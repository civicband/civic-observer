import pytest
from django.db import IntegrityError

from .models import Muni


@pytest.mark.django_db
class TestMuniModel:
    def test_create_muni(self):
        muni = Muni.objects.create(
            subdomain="testcity",
            name="Test City",
            state="CA",
            country="USA",
            kind="city",
            pages=100,
        )
        assert muni.subdomain == "testcity"
        assert muni.name == "Test City"
        assert muni.state == "CA"
        assert muni.country == "USA"
        assert muni.kind == "city"
        assert muni.pages == 100
        assert muni.created is not None
        assert muni.modified is not None
        assert str(muni.id)  # UUID is valid

    def test_str_representation(self):
        muni = Muni(name="San Francisco", state="CA")
        assert str(muni) == "San Francisco, CA"

    def test_subdomain_unique(self):
        Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        with pytest.raises(IntegrityError):
            Muni.objects.create(
                subdomain="testcity", name="Another City", state="NY", kind="city"
            )

    def test_default_values(self):
        muni = Muni.objects.create(
            subdomain="testmuni", name="Test Municipality", state="TX", kind="city"
        )
        assert muni.country == "USA"
        assert muni.pages == 0
        assert muni.latitude is None
        assert muni.longitude is None
        assert muni.popup_data is None

    def test_optional_fields(self):
        muni = Muni.objects.create(
            subdomain="fulltest",
            name="Full Test City",
            state="NY",
            kind="city",
            latitude=40.7128,
            longitude=-74.0060,
            popup_data={"population": 8000000},
        )
        assert muni.latitude == 40.7128
        assert muni.longitude == -74.0060
        assert muni.popup_data == {"population": 8000000}

    def test_meta_options(self):
        assert Muni._meta.verbose_name == "Municipality"
        assert Muni._meta.verbose_name_plural == "Municipalities"
        assert Muni._meta.ordering == ["name"]
