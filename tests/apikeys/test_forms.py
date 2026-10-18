import pytest

from apikeys.forms import APIKeyCreateForm
from tests.factories import MuniFactory


@pytest.mark.django_db
class TestAPIKeyCreateForm:
    def test_all_sites_defaults_to_unrestricted(self):
        form = APIKeyCreateForm(data={"name": "All Sites"})

        assert form.is_valid(), form.errors
        assert form.cleaned_data["all_sites"] is True
        assert form.cleaned_data["site_objects"] == []

    def test_all_sites_ignores_site_list(self):
        muni = MuniFactory(subdomain="alameda.ca")
        form = APIKeyCreateForm(
            data={"name": "All Sites", "all_sites": "on", "sites": muni.subdomain}
        )

        assert form.is_valid(), form.errors
        assert form.cleaned_data["all_sites"] is True
        assert form.cleaned_data["site_objects"] == []

    def test_scoped_key_requires_at_least_one_site(self):
        form = APIKeyCreateForm(data={"name": "Scoped", "all_sites": "false"})

        assert form.is_valid() is False
        assert "sites" in form.errors

    def test_scoped_key_resolves_known_subdomains(self):
        alameda = MuniFactory(subdomain="alameda.ca")
        oakland = MuniFactory(subdomain="oakland.ca")
        form = APIKeyCreateForm(
            data={
                "name": "Scoped",
                "all_sites": "false",
                "sites": "alameda.ca, oakland.ca, alameda.ca",
            }
        )

        assert form.is_valid(), form.errors
        assert form.cleaned_data["all_sites"] is False
        assert set(form.cleaned_data["site_objects"]) == {alameda, oakland}

    def test_scoped_key_rejects_unknown_subdomains(self):
        form = APIKeyCreateForm(
            data={
                "name": "Scoped",
                "all_sites": "false",
                "sites": "missing.ca",
            }
        )

        assert form.is_valid() is False
        assert "Unknown site subdomains: missing.ca" in form.errors["sites"][0]
