from datetime import timedelta

import pytest
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone

from municipalities.models import Muni
from tests.factories import (
    MuniFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
)
from watch.models import Place, Subreddit


def _changelist_pks(response):
    return {obj.pk for obj in response.context["cl"].result_list}


def _filter_names(model_admin):
    return {getattr(item, "parameter_name", item) for item in model_admin.list_filter}


@pytest.mark.django_db
def test_subreddit_admin_accept_suggestions(admin_client):
    row = Subreddit.objects.create(
        name="chicoca", kind="place", source="gazetteer", confidence="high"
    )
    old = timezone.now() - timedelta(days=1)
    Subreddit.objects.filter(pk=row.pk).update(modified=old)

    admin_client.post(
        reverse("admin:watch_subreddit_changelist"),
        {"action": "accept_suggestions", "_selected_action": [row.pk]},
    )

    row.refresh_from_db()
    assert row.reviewed_at is not None
    assert row.modified > old


@pytest.mark.django_db
def test_subreddit_needs_review_filter(admin_client):
    SubredditFactory(name="reviewed", reviewed_at="2024-01-01T00:00:00Z")
    pending = SubredditFactory(name="pending")

    response = admin_client.get(
        reverse("admin:watch_subreddit_changelist"), {"needs_review": "1"}
    )

    assert response.status_code == 200
    assert _changelist_pks(response) == {pending.pk}


@pytest.mark.django_db
def test_place_admin_filters_unlinked(admin_client):
    unlinked = PlaceFactory(name="Unlinked", muni_link_status="unlinked")
    no_match = PlaceFactory(name="No match", muni_link_status="no_match")
    PlaceFactory(name="Auto", muni_link_status="auto")
    PlaceFactory(name="Confirmed", muni_link_status="confirmed")

    response = admin_client.get(
        reverse("admin:watch_place_changelist"), {"needs_review": "1"}
    )

    assert response.status_code == 200
    assert _changelist_pks(response) == {unlinked.pk, no_match.pk}


@pytest.mark.django_db
def test_muni_admin_watch_link_filter(admin_client):
    unlinked = MuniFactory(name="Unlinked City")
    linked = MuniFactory(name="Linked City")
    PlaceWithMuniFactory(muni=linked)

    response = admin_client.get(
        reverse("admin:municipalities_muni_changelist"), {"watch_link": "unlinked"}
    )

    assert response.status_code == 200
    assert _changelist_pks(response) == {unlinked.pk}
    assert linked.pk not in _changelist_pks(response)


@pytest.mark.django_db
def test_place_admin_filter_accepts_explicit_statuses(admin_client):
    unlinked = PlaceFactory(name="Unlinked", muni_link_status="unlinked")
    PlaceFactory(name="Auto", muni_link_status="auto")

    response = admin_client.get(
        reverse("admin:watch_place_changelist"),
        {"muni_link_status__in": "unlinked,no_match"},
    )

    assert response.status_code == 200
    assert _changelist_pks(response) == {unlinked.pk}


def test_review_admin_configuration():
    subreddit_admin = admin.site._registry[Subreddit]
    place_admin = admin.site._registry[Place]
    muni_admin = admin.site._registry[Muni]

    assert subreddit_admin.autocomplete_fields == ["place"]  # type: ignore[attr-defined]
    assert subreddit_admin.search_fields == ["name", "place__name", "note"]  # type: ignore[attr-defined]
    assert "accept_suggestions" in subreddit_admin.actions  # type: ignore[attr-defined]
    assert {"kind", "source", "muted", "reviewed_at"} <= _filter_names(
        subreddit_admin  # type: ignore[arg-type]
    )
    assert "needs_review" in _filter_names(subreddit_admin)  # type: ignore[arg-type]

    assert place_admin.autocomplete_fields == ["county", "muni"]  # type: ignore[attr-defined]
    assert place_admin.search_fields == ["name", "geoid", "canonical_key"]  # type: ignore[attr-defined]
    assert {"place_type", "state", "is_government", "muni_link_status"} <= (
        _filter_names(place_admin)  # type: ignore[arg-type]
    )
    assert "needs_review" in _filter_names(place_admin)  # type: ignore[arg-type]

    assert "watch_link" in _filter_names(muni_admin)  # type: ignore[arg-type]
    assert muni_admin.search_fields  # type: ignore[attr-defined]

    # Autocomplete endpoints require search_fields on the related admins.
    assert admin.site._registry[Place].get_search_fields(None)  # type: ignore[attr-defined]
