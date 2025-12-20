from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from municipalities.models import Muni


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
