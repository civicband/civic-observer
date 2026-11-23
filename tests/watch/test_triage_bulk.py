"""Tests for bulk triage actions."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import ThreadFactory
from watch.models import Thread


def bulk_url():
    return reverse("watch:bulk")


@pytest.mark.django_db
def test_bulk_close_requires_staff(authenticated_client):
    thread = ThreadFactory()

    response = authenticated_client.post(
        bulk_url(), {"action": "close", "thread_id": [str(thread.pk)]}
    )

    assert response.status_code in {302, 403}
    thread.refresh_from_db()
    assert thread.status == Thread.Status.NEW


@pytest.mark.django_db
def test_bulk_close_rejects_an_empty_selection(admin_client):
    response = admin_client.post(bulk_url(), {"action": "close"})

    assert response.status_code == 400


@pytest.mark.django_db
def test_bulk_close_rejects_an_unknown_action(admin_client):
    thread = ThreadFactory()

    response = admin_client.post(
        bulk_url(), {"action": "reopen", "thread_id": [str(thread.pk)]}
    )

    assert response.status_code == 400
    thread.refresh_from_db()
    assert thread.status == Thread.Status.NEW


@pytest.mark.django_db
def test_bulk_close_only_updates_selected_threads(admin_client):
    selected = ThreadFactory()
    untouched = ThreadFactory()

    response = admin_client.post(
        bulk_url(), {"action": "close", "thread_id": [str(selected.pk)]}
    )

    assert response.status_code == 302
    selected.refresh_from_db()
    untouched.refresh_from_db()
    assert selected.status == Thread.Status.DISMISSED
    assert selected.dismissed_at is not None
    assert untouched.status == Thread.Status.NEW
    assert untouched.dismissed_at is None


@pytest.mark.django_db
def test_bulk_close_accepts_compact_thread_ids(admin_client):
    selected = ThreadFactory()

    response = admin_client.post(
        bulk_url(), {"action": "close", "thread_ids": str(selected.pk)}
    )

    assert response.status_code == 302
    selected.refresh_from_db()
    assert selected.status == Thread.Status.DISMISSED


@pytest.mark.django_db
def test_bulk_close_deduplicates_compact_and_individual_ids(admin_client):
    selected = ThreadFactory()

    response = admin_client.post(
        bulk_url(),
        {
            "action": "close",
            "thread_id": [str(selected.pk)],
            "thread_ids": str(selected.pk),
        },
    )

    assert response.status_code == 302
    selected.refresh_from_db()
    assert selected.status == Thread.Status.DISMISSED


@pytest.mark.django_db
def test_bulk_close_rejects_invalid_compact_thread_ids(admin_client):
    response = admin_client.post(
        bulk_url(), {"action": "close", "thread_ids": "not-a-uuid"}
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_bulk_close_compact_ids_respect_the_current_filter(admin_client):
    ten_days = timezone.now() - timedelta(days=10)
    stale = ThreadFactory(first_seen=ten_days, last_seen=ten_days)
    fresh = ThreadFactory()

    admin_client.post(
        f"{bulk_url()}?age=1",
        {"action": "close", "thread_ids": f"{stale.pk},{fresh.pk}"},
    )

    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == Thread.Status.DISMISSED
    assert fresh.status == Thread.Status.NEW


@pytest.mark.django_db
def test_bulk_close_clears_snooze_and_updates_modified(admin_client):
    before = timezone.now()
    thread = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() - timedelta(minutes=1),
    )

    admin_client.post(bulk_url(), {"action": "close", "thread_id": [str(thread.pk)]})

    thread.refresh_from_db()
    assert thread.status == Thread.Status.DISMISSED
    assert thread.snoozed_until is None
    assert thread.dismissed_at is not None
    assert thread.modified >= before


@pytest.mark.django_db
def test_bulk_close_ignores_ids_outside_the_current_filter(admin_client):
    ten_days = timezone.now() - timedelta(days=10)
    stale = ThreadFactory(first_seen=ten_days, last_seen=ten_days)
    fresh = ThreadFactory()

    admin_client.post(
        f"{bulk_url()}?age=1",
        {"action": "close", "thread_id": [str(stale.pk), str(fresh.pk)]},
    )

    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == Thread.Status.DISMISSED
    assert fresh.status == Thread.Status.NEW


@pytest.mark.django_db
def test_bulk_close_ignores_threads_no_longer_queue_eligible(admin_client):
    due = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() - timedelta(minutes=1),
    )
    future = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() + timedelta(days=1),
    )
    already_closed = ThreadFactory(status=Thread.Status.DISMISSED)
    already_closed_modified = already_closed.modified

    admin_client.post(
        bulk_url(),
        {
            "action": "close",
            "thread_id": [str(due.pk), str(future.pk), str(already_closed.pk)],
        },
    )

    due.refresh_from_db()
    future.refresh_from_db()
    already_closed.refresh_from_db()
    assert due.status == Thread.Status.DISMISSED
    assert due.snoozed_until is None
    assert future.status == Thread.Status.SNOOZED
    assert future.snoozed_until is not None
    assert already_closed.status == Thread.Status.DISMISSED
    assert already_closed.modified == already_closed_modified


@pytest.mark.django_db
def test_bulk_close_htmx_returns_the_queue_and_refreshes_the_detail(admin_client):
    selected = ThreadFactory(title="Bulk selected", last_seen=timezone.now())
    remaining = ThreadFactory(
        title="Still queued", last_seen=timezone.now() - timedelta(minutes=1)
    )

    response = admin_client.post(
        bulk_url(),
        {"action": "close", "thread_id": [str(selected.pk)]},
        HTTP_HX_REQUEST="true",
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Still queued" in body
    assert "Bulk selected" not in body
    assert 'id="triage-detail"' in body
    assert 'hx-swap-oob="innerHTML"' in body
    assert response.context["thread"] == remaining


@pytest.mark.django_db
def test_bulk_close_htmx_keeps_the_selected_detail_thread_when_still_queued(
    admin_client,
):
    selected = ThreadFactory(title="Bulk selected", last_seen=timezone.now())
    reading = ThreadFactory(
        title="Reading this one", last_seen=timezone.now() - timedelta(minutes=1)
    )

    response = admin_client.post(
        f"{bulk_url()}?thread={reading.pk}",
        {"action": "close", "thread_id": [str(selected.pk)]},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert response.context["thread"] == reading
    assert "Reading this one" in response.content.decode()


@pytest.mark.django_db
def test_bulk_close_htmx_uses_the_current_page_url_for_detail(admin_client):
    selected = ThreadFactory(title="Bulk selected", last_seen=timezone.now())
    reading = ThreadFactory(
        title="Reading this one", last_seen=timezone.now() - timedelta(minutes=1)
    )
    stale = ThreadFactory(
        title="Stale form target", last_seen=timezone.now() - timedelta(minutes=2)
    )

    response = admin_client.post(
        f"{bulk_url()}?thread={stale.pk}",
        {"action": "close", "thread_id": [str(selected.pk)]},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=(
            f"http://testserver{reverse('watch:triage')}?thread={reading.pk}"
        ),
    )

    assert response.status_code == 200
    assert response.context["thread"] == reading


@pytest.mark.django_db
def test_bulk_close_htmx_uses_head_when_current_url_has_no_thread(admin_client):
    selected = ThreadFactory(title="Bulk selected", last_seen=timezone.now())
    remaining = ThreadFactory(
        title="Still queued", last_seen=timezone.now() - timedelta(minutes=1)
    )
    stale = ThreadFactory(
        title="Stale form target", last_seen=timezone.now() - timedelta(minutes=2)
    )

    response = admin_client.post(
        f"{bulk_url()}?thread={stale.pk}",
        {"action": "close", "thread_id": [str(selected.pk)]},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=(f"http://testserver{reverse('watch:triage')}?age=1"),
    )

    assert response.status_code == 200
    assert response.context["thread"] == remaining


@pytest.mark.django_db
def test_bulk_close_redirects_with_filters_for_plain_posts(admin_client):
    thread = ThreadFactory()

    response = admin_client.post(
        f"{bulk_url()}?covered=no&sort=oldest",
        {"action": "close", "thread_id": [str(thread.pk)]},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('watch:triage')}?covered=no&sort=oldest"


@pytest.mark.django_db
def test_bulk_close_handles_a_malformed_current_url(admin_client):
    selected = ThreadFactory(title="Selected", last_seen=timezone.now())
    remaining = ThreadFactory(
        title="Still queued", last_seen=timezone.now() - timedelta(minutes=1)
    )

    response = admin_client.post(
        bulk_url(),
        {"action": "close", "thread_id": [str(selected.pk)]},
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://[",
    )

    assert response.status_code == 200
    assert response.context["thread"] == remaining


@pytest.mark.django_db
def test_bulk_close_rejects_more_than_the_limit(admin_client, monkeypatch):
    monkeypatch.setattr("watch.views.BULK_CLOSE_LIMIT", 1)
    first = ThreadFactory()
    second = ThreadFactory()

    response = admin_client.post(
        bulk_url(),
        {"action": "close", "thread_id": [str(first.pk), str(second.pk)]},
    )

    assert response.status_code == 400
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Thread.Status.NEW
    assert second.status == Thread.Status.NEW


@pytest.mark.django_db
def test_bulk_close_is_post_only(admin_client):
    assert admin_client.get(bulk_url()).status_code == 405
