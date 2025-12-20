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
