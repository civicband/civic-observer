import json
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from municipalities.models import Muni
from tests.factories import AdminUserFactory, MuniFactory, UserFactory


@pytest.fixture
def municipalities(db):
    """Create test municipalities."""
    now = timezone.now()
    return [
        Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
            last_updated=now,
        ),
        Muni.objects.create(
            subdomain="portland",
            name="Portland",
            state="OR",
            kind="City",
            pages=75,
            last_updated=now - timedelta(days=40),
        ),
    ]


class TestMuniListView:
    def test_list_all_municipalities(self, client: Client, municipalities):
        """List view shows all municipalities."""
        response = client.get(reverse("munis:muni-list"))
        assert response.status_code == 200
        assert "Oakland" in response.content.decode()
        assert "Portland" in response.content.decode()

    def test_filter_by_state(self, client: Client, municipalities):
        """Filter by state returns only matching municipalities."""
        response = client.get(reverse("munis:muni-list"), {"state": "CA"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Portland" not in content

    def test_search_by_name(self, client: Client, municipalities):
        """Search by name returns matching municipalities."""
        response = client.get(reverse("munis:muni-list"), {"q": "oak"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Portland" not in content

    def test_pagination_default_25_per_page(self, client: Client, db):
        """Pagination shows 25 municipalities per page."""
        # Create 30 municipalities
        for i in range(30):
            Muni.objects.create(
                subdomain=f"city-{i}",
                name=f"City {i}",
                state="CA",
                kind="City",
                pages=i,
            )
        response = client.get(reverse("munis:muni-list"))
        assert response.status_code == 200
        # Should have page_obj in context
        assert "page_obj" in response.context
        assert response.context["page_obj"].paginator.per_page == 25
        assert response.context["page_obj"].paginator.num_pages == 2

    def test_pagination_page_2(self, client: Client, db):
        """Can navigate to page 2."""
        for i in range(30):
            Muni.objects.create(
                subdomain=f"city-{i}",
                name=f"City {i:02d}",  # Zero-pad for sorting
                state="CA",
                kind="City",
                pages=i,
            )
        response = client.get(reverse("munis:muni-list"), {"page": "2"})
        assert response.status_code == 200
        assert response.context["page_obj"].number == 2


class TestMuniListViewLinks:
    def test_name_links_to_civic_band_with_utm(self, client: Client, db):
        """Municipality name links to civic.band with UTM parameters."""
        Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
            last_updated=timezone.now(),
        )
        response = client.get(reverse("munis:muni-list"))
        content = response.content.decode()
        assert "https://oakland.civic.band" in content
        assert "utm_source=civicobserver" in content
        assert "utm_medium=municipalities" in content
        assert "utm_campaign=municipality_list" in content


class TestMuniWebhookUpdateView:
    @pytest.mark.django_db
    @patch.dict("os.environ", {"WEBHOOK_SECRET": "test-secret"})
    @patch("django_rq.get_queue")
    def test_webhook_uses_new_backfill_system(self, mock_get_queue):
        """Test that webhook triggers new backfill orchestrator."""
        import json

        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue
        mock_job = Mock(id="job123")
        mock_queue.enqueue.return_value = mock_job

        # Create new municipality via webhook
        client = Client()
        response = client.post(
            "/munis/api/update/new-city/",
            data=json.dumps(
                {
                    "name": "New City",
                    "state": "CA",
                    "pages": 100,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-secret",
        )

        assert response.status_code == 201

        # Verify backfill_municipality_meetings_task was enqueued
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args[0]
        assert call_args[0].__name__ == "backfill_municipality_meetings_task"


class TestMuniCRUDPermissions:
    """Creating, updating, and deleting municipalities is staff-only."""

    def test_anonymous_cannot_create(self, client: Client, db):
        response = client.get(reverse("munis:muni-create"))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_authenticated_non_staff_cannot_create(self, client: Client, db):
        client.force_login(UserFactory())
        response = client.get(reverse("munis:muni-create"))
        assert response.status_code == 403

    def test_authenticated_non_staff_cannot_update(self, client: Client, db):
        muni = MuniFactory()
        client.force_login(UserFactory())
        response = client.get(reverse("munis:muni-update", kwargs={"pk": muni.pk}))
        assert response.status_code == 403

    def test_authenticated_non_staff_cannot_delete(self, client: Client, db):
        muni = MuniFactory()
        client.force_login(UserFactory())
        response = client.get(reverse("munis:muni-delete", kwargs={"pk": muni.pk}))
        assert response.status_code == 403

    def test_staff_can_create(self, client: Client, db):
        client.force_login(AdminUserFactory())
        response = client.get(reverse("munis:muni-create"))
        assert response.status_code == 200


@pytest.fixture
def webhook_client(client, settings, monkeypatch):
    """Return an authenticated client for the deploy webhook."""
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret-123")
    client.defaults["HTTP_AUTHORIZATION"] = "Bearer test-secret-123"
    return client


def _post_webhook(client, subdomain, payload):
    url = reverse("munis:muni-webhook-update", kwargs={"subdomain": subdomain})
    return client.post(url, json.dumps(payload), content_type="application/json")


@pytest.mark.django_db
def test_webhook_stores_country_name_as_iso_code(webhook_client):
    """civic-band forwards sites.country verbatim, often as "Canada"."""
    payload = {
        "name": "Calgary",
        "state": "AB",
        "country": "Canada",
        "kind": "city",
        "pages": 10,
    }

    response = _post_webhook(webhook_client, "calgary.ab.canada", payload)

    assert response.status_code == 201
    muni = Muni.objects.get(subdomain="calgary.ab.canada")
    assert str(muni.country) == "CA"
    assert response.json()["country"] == "CA"


@pytest.mark.parametrize("country", ["CA", "ca", "CAN", " Canada ", "canada"])
@pytest.mark.django_db
def test_webhook_accepts_any_country_spelling(webhook_client, country):
    payload = {
        "name": "Calgary",
        "state": "AB",
        "country": country,
        "kind": "city",
        "pages": 10,
    }

    response = _post_webhook(webhook_client, "calgary.ab.canada", payload)

    assert response.status_code == 201
    assert str(Muni.objects.get(subdomain="calgary.ab.canada").country) == "CA"


@pytest.mark.django_db
def test_webhook_falls_back_to_the_state_for_an_unknown_country(webhook_client):
    """Postel's Law: one bad field must not fail the whole deploy webhook."""
    payload = {
        "name": "Calgary",
        "state": "AB",
        "country": "Atlantis",
        "kind": "city",
        "pages": 3,
    }

    response = _post_webhook(webhook_client, "calgary.ab.canada", payload)

    assert response.status_code == 201
    assert str(Muni.objects.get(subdomain="calgary.ab.canada").country) == "CA"


@pytest.mark.django_db
def test_webhook_keeps_the_stored_country_for_an_unknown_country(webhook_client):
    Muni.objects.create(
        subdomain="bryan.ga", name="Bryan", state="GA", country="US", kind="city"
    )
    payload = {
        "name": "Bryan County",
        "state": "GA",
        "country": "Atlantis",
        "kind": "county",
        "pages": 0,
    }

    response = _post_webhook(webhook_client, "bryan.ga", payload)

    assert response.status_code == 200
    muni = Muni.objects.get(subdomain="bryan.ga")
    assert muni.name == "Bryan County"
    assert str(muni.country) == "US"


@pytest.mark.django_db
def test_webhook_uses_the_default_for_an_unknown_country(webhook_client):
    payload = {
        "name": "Nowhere",
        "state": "GA",
        "country": "Atlantis",
        "kind": "city",
        "pages": 0,
    }

    response = _post_webhook(webhook_client, "nowhere.ga", payload)

    assert response.status_code == 201
    assert str(Muni.objects.get(subdomain="nowhere.ga").country) == "US"


@pytest.mark.django_db
def test_webhook_never_stores_an_unresolvable_country(webhook_client):
    payload = {
        "name": "Nowhere",
        "state": "GA",
        "country": "Atlantis",
        "kind": "city",
        "pages": 0,
    }

    _post_webhook(webhook_client, "nowhere.ga", payload)

    assert not Muni.objects.filter(country="Atlantis").exists()


@pytest.mark.django_db
def test_webhook_repairs_a_previously_unresolvable_country(webhook_client):
    Muni.objects.create(
        subdomain="calgary.ab.canada",
        name="Calgary",
        state="AB",
        country="US",
        kind="city",
    )
    payload = {
        "name": "Calgary",
        "state": "AB",
        "country": "Atlantis",
        "kind": "city",
        "pages": 0,
    }

    _post_webhook(webhook_client, "calgary.ab.canada", payload)

    muni = Muni.objects.get(subdomain="calgary.ab.canada")
    assert str(muni.country) == "CA"


@pytest.mark.django_db
def test_webhook_repairs_a_country_already_stored_as_a_name(webhook_client):
    muni = Muni.objects.create(
        subdomain="calgary.ab.canada",
        name="Calgary",
        state="AB",
        country="US",
        kind="city",
    )
    payload = {
        "name": "Calgary",
        "state": "AB",
        "country": "Canada",
        "kind": "city",
        "pages": 0,
    }

    response = _post_webhook(webhook_client, "calgary.ab.canada", payload)

    assert response.status_code == 200
    muni.refresh_from_db()
    assert str(muni.country) == "CA"


@pytest.mark.django_db
def test_webhook_infers_canada_when_country_is_omitted(webhook_client):
    payload = {"name": "Calgary", "state": "AB", "kind": "city", "pages": 0}

    response = _post_webhook(webhook_client, "calgary.ab.canada", payload)

    assert response.status_code == 201
    assert str(Muni.objects.get(subdomain="calgary.ab.canada").country) == "CA"
