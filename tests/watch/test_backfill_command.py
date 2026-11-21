"""Tests for the ``backfill_watch_threads`` operator command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ThreadFactory, ThreadPlaceFactory

MODULE = "watch.management.commands.backfill_watch_threads.enqueue_enrichment"


def _run(*args: str, **options):
    stdout = StringIO()
    stderr = StringIO()
    call_command(
        "backfill_watch_threads", *args, stdout=stdout, stderr=stderr, **options
    )
    return stdout.getvalue(), stderr.getvalue()


@pytest.mark.django_db
def test_default_selects_threads_without_thread_places(monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr(MODULE, enqueue)
    empty = ThreadFactory()
    enriched = ThreadFactory()
    ThreadPlaceFactory(thread=enriched)

    out, _ = _run()

    assert enqueue.call_count == 1
    assert enqueue.call_args.args == (str(empty.id),)
    assert "enqueued=1" in out
    assert "skipped=1" in out
    assert "failed=0" in out


@pytest.mark.django_db
def test_all_includes_threads_that_already_have_thread_places(monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr(MODULE, enqueue)
    first = ThreadFactory()
    second = ThreadFactory()
    ThreadPlaceFactory(thread=second)

    out, _ = _run("--all")

    assert {call.args[0] for call in enqueue.call_args_list} == {
        str(first.id),
        str(second.id),
    }
    assert "enqueued=2" in out
    assert "skipped=0" in out


@pytest.mark.django_db
def test_since_filters_by_last_seen(monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr(MODULE, enqueue)
    old = ThreadFactory(last_seen=timezone.now() - timedelta(days=30))
    recent = ThreadFactory(last_seen=timezone.now())
    since = (timezone.localdate() - timedelta(days=1)).isoformat()

    _run("--since", since)

    assert [call.args[0] for call in enqueue.call_args_list] == [str(recent.id)]
    assert str(old.id) not in {call.args[0] for call in enqueue.call_args_list}


@pytest.mark.django_db
def test_limit_keeps_newest_threads_first(monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr(MODULE, enqueue)
    now = timezone.now()
    oldest = ThreadFactory(last_seen=now - timedelta(days=2))
    middle = ThreadFactory(last_seen=now - timedelta(days=1))
    newest = ThreadFactory(last_seen=now)

    _run("--limit", "2")

    assert [call.args[0] for call in enqueue.call_args_list] == [
        str(newest.id),
        str(middle.id),
    ]
    assert str(oldest.id) not in {call.args[0] for call in enqueue.call_args_list}


@pytest.mark.django_db
def test_dry_run_reports_selection_without_enqueuing(monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr(MODULE, enqueue)
    ThreadFactory()
    ThreadFactory()

    out, _ = _run("--dry-run")

    assert enqueue.call_count == 0
    assert out.startswith("dry-run ")
    assert "selected=2" in out
    assert "enqueued=0" in out
    assert "skipped=0" in out
    assert "failed=0" in out


@pytest.mark.django_db
def test_enqueue_failure_is_counted_and_reported(monkeypatch):
    enqueue = Mock(side_effect=["job-1", None])
    monkeypatch.setattr(MODULE, enqueue)
    first = ThreadFactory(last_seen=timezone.now())
    second = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))

    out, err = _run()

    assert enqueue.call_count == 2
    assert "enqueued=1" in out
    assert "failed=1" in out
    assert str(second.id) in err
    assert str(first.id) not in err
