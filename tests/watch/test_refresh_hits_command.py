"""Tests for the ``refresh_watch_hits`` operator command."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import ThreadPlace

COMMAND = "watch.management.commands.refresh_watch_hits.refresh_thread_hits"


def _covered_subject(thread):
    """Give ``thread`` one subject place with a civic.band municipality."""
    return ThreadPlaceFactory(
        thread=thread,
        place=PlaceWithMuniFactory(),
        role=ThreadPlace.Role.SUBJECT,
    )


@pytest.mark.django_db
def test_refresh_command_for_one_thread(monkeypatch, capsys):
    thread = ThreadFactory()
    monkeypatch.setattr(COMMAND, lambda t: 2)

    call_command("refresh_watch_hits", "--thread", str(thread.id))

    out = capsys.readouterr().out
    assert "2 hits" in out
    assert thread.key in out


@pytest.mark.django_db
def test_refresh_command_accepts_repeated_thread_option(monkeypatch):
    first = ThreadFactory()
    second = ThreadFactory()
    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)

    call_command(
        "refresh_watch_hits",
        "--thread",
        str(first.id),
        "--thread",
        str(second.id),
    )

    assert refreshed == [first.id, second.id]


@pytest.mark.django_db
def test_refresh_command_reports_missing_thread_without_refreshing(monkeypatch):
    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)
    stderr = StringIO()

    call_command(
        "refresh_watch_hits",
        "--thread",
        "00000000-0000-0000-0000-000000000000",
        stderr=stderr,
    )

    assert refreshed == []
    assert "not found" in stderr.getvalue().lower()


@pytest.mark.django_db
def test_refresh_command_reports_malformed_thread_id(monkeypatch):
    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)
    stderr = StringIO()

    call_command("refresh_watch_hits", "--thread", "not-a-uuid", stderr=stderr)

    assert refreshed == []
    assert "not found" in stderr.getvalue().lower()


@pytest.mark.django_db
def test_refresh_command_warns_without_thread_or_all(monkeypatch):
    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)
    stderr = StringIO()

    call_command("refresh_watch_hits", stderr=stderr)

    assert refreshed == []
    assert "--thread" in stderr.getvalue()
    assert "--all" in stderr.getvalue()


@pytest.mark.django_db
def test_refresh_command_all_uses_covered_threads_newest_first(monkeypatch):
    now = timezone.now()
    older = ThreadFactory(last_seen=now - timedelta(days=2))
    newer = ThreadFactory(last_seen=now - timedelta(days=1))
    _covered_subject(older)
    _covered_subject(newer)

    uncovered = ThreadFactory(last_seen=now)
    ThreadPlaceFactory(
        thread=uncovered,
        place=PlaceFactory(),
        role=ThreadPlace.Role.SUBJECT,
    )
    merely_mentioned = ThreadFactory(last_seen=now)
    ThreadPlaceFactory(
        thread=merely_mentioned,
        place=PlaceWithMuniFactory(),
        role=ThreadPlace.Role.MENTIONED,
    )

    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)

    call_command("refresh_watch_hits", "--all")

    assert refreshed == [newer.id, older.id]


@pytest.mark.django_db
def test_refresh_command_all_honours_limit(monkeypatch):
    now = timezone.now()
    threads = [ThreadFactory(last_seen=now - timedelta(days=n)) for n in range(3)]
    for thread in threads:
        _covered_subject(thread)

    refreshed = []
    monkeypatch.setattr(COMMAND, lambda t: refreshed.append(t.id) or 0)

    call_command("refresh_watch_hits", "--all", "--limit", "2")

    assert refreshed == [threads[0].id, threads[1].id]


@pytest.mark.django_db
def test_refresh_command_all_prints_summary(monkeypatch, capsys):
    thread = ThreadFactory()
    _covered_subject(thread)
    monkeypatch.setattr(COMMAND, lambda t: 3)

    call_command("refresh_watch_hits", "--all")

    out = capsys.readouterr().out
    assert "3 hits" in out
    assert "1 threads" in out
    assert "3 hits total" in out


@pytest.mark.django_db
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_refresh_command_rejects_non_positive_limit(limit):
    with pytest.raises(CommandError, match="--limit must be a positive integer"):
        call_command("refresh_watch_hits", "--all", "--limit", limit)
