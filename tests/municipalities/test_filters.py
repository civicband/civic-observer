from datetime import timedelta

import pytest
from django.utils import timezone

from municipalities.filters import MuniFilter
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
            subdomain="berkeley",
            name="Berkeley",
            state="CA",
            kind="City",
            pages=50,
            last_updated=now - timedelta(days=10),
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


class TestMuniFilter:
    def test_filter_by_state(self, municipalities):
        """Filter municipalities by state."""
        qs = Muni.objects.all()
        f = MuniFilter({"state": "CA"}, queryset=qs)
        assert f.qs.count() == 2
        assert all(m.state == "CA" for m in f.qs)

    def test_filter_by_kind(self, municipalities):
        """Filter municipalities by type/kind."""
        # Add a county for testing
        Muni.objects.create(
            subdomain="alameda-county",
            name="Alameda County",
            state="CA",
            kind="County",
            pages=200,
        )
        qs = Muni.objects.all()
        f = MuniFilter({"kind": "City"}, queryset=qs)
        assert f.qs.count() == 3
        assert all(m.kind == "City" for m in f.qs)

    def test_filter_by_activity_7_days(self, municipalities):
        """Filter municipalities updated in last 7 days."""
        qs = Muni.objects.all()
        f = MuniFilter({"activity": "7"}, queryset=qs)
        # Only Oakland was updated today
        assert f.qs.count() == 1
        assert f.qs.first().name == "Oakland"

    def test_filter_by_activity_30_days(self, municipalities):
        """Filter municipalities updated in last 30 days."""
        qs = Muni.objects.all()
        f = MuniFilter({"activity": "30"}, queryset=qs)
        # Oakland (today) and Berkeley (10 days ago)
        assert f.qs.count() == 2

    def test_filter_by_activity_90_days(self, municipalities):
        """Filter municipalities updated in last 90 days."""
        qs = Muni.objects.all()
        f = MuniFilter({"activity": "90"}, queryset=qs)
        # All three municipalities
        assert f.qs.count() == 3

    def test_search_by_name(self, municipalities):
        """Search municipalities by name."""
        qs = Muni.objects.all()
        f = MuniFilter({"q": "oak"}, queryset=qs)
        assert f.qs.count() == 1
        assert f.qs.first().name == "Oakland"

    def test_search_by_subdomain(self, municipalities):
        """Search municipalities by subdomain."""
        qs = Muni.objects.all()
        f = MuniFilter({"q": "port"}, queryset=qs)
        assert f.qs.count() == 1
        assert f.qs.first().subdomain == "portland"

    def test_search_case_insensitive(self, municipalities):
        """Search is case insensitive."""
        qs = Muni.objects.all()
        f = MuniFilter({"q": "BERKELEY"}, queryset=qs)
        assert f.qs.count() == 1
        assert f.qs.first().name == "Berkeley"

    def test_filter_by_activity_excludes_null_dates(self, municipalities):
        """Municipalities with null last_updated are excluded from activity filter."""
        Muni.objects.create(
            subdomain="new-city", name="New City", state="CA", kind="City", pages=10
        )
        qs = Muni.objects.all()
        f = MuniFilter({"activity": "7"}, queryset=qs)
        # Should only include Oakland (has recent last_updated)
        # Should exclude New City (has null last_updated)
        assert f.qs.count() == 1
        assert f.qs.first().name == "Oakland"
        assert all(m.last_updated is not None for m in f.qs)
