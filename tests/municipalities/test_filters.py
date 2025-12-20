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
