"""Tests for the ``watch_extraction_report`` operator command."""

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import PlaceMention, ThreadLabel


def _mention_place(mention, place, **overrides) -> PlaceMention:
    """Record one extracted place on ``mention``."""
    fields = {
        "mention": mention,
        "place": place,
        "matched_text": place.name,
        "method": PlaceMention.Method.BODY_PATTERN,
        "confidence": PlaceMention.Confidence.HIGH,
        "is_subject": True,
    }
    fields.update(overrides)
    return PlaceMention.objects.create(**fields)


def _row(out: str, group: str) -> dict[str, str]:
    """Parse the table row that starts with ``group``."""
    line = next(line for line in out.splitlines() if line.startswith(group))
    columns = line.split()
    return dict(
        zip(
            ("group", "precision", "recall", "tp", "fp", "fn", "labels"),
            columns,
            strict=True,
        )
    )


@pytest.mark.django_db
def test_report_prints_method_metrics(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    MentionFactory(thread=thread)
    PlaceMention.objects.create(
        mention=thread.mentions.first(),
        place=place,
        matched_text="Chico",
        method="body_pattern",
        confidence="high",
        is_subject=True,
    )
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    call_command("watch_extraction_report")
    out = capsys.readouterr().out
    assert "body_pattern" in out
    assert "precision" in out
    assert "1.00" in out


@pytest.mark.django_db
def test_report_counts_false_positives_and_negatives(capsys):
    thread = ThreadFactory()
    predicted = PlaceFactory()
    labelled = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), predicted)
    ThreadLabel.objects.create(thread=thread, place=labelled, is_subject=True)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "body_pattern")
    assert row == {
        "group": "body_pattern",
        "precision": "0.00",
        "recall": "0.00",
        "tp": "0",
        "fp": "1",
        "fn": "1",
        "labels": "1",
    }


@pytest.mark.django_db
def test_report_requires_matching_is_subject(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), place, is_subject=True)
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=False)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "body_pattern")
    assert row["tp"] == "0"
    assert row["fp"] == "1"
    assert row["fn"] == "1"


@pytest.mark.django_db
def test_report_groups_thread_place_predictions_by_source(capsys):
    thread = ThreadFactory()
    matched = PlaceFactory()
    extra = PlaceFactory()
    ThreadPlaceFactory(
        thread=thread,
        place=matched,
        role="subject",
        source="post",
    )
    ThreadPlaceFactory(
        thread=thread,
        place=extra,
        role="mentioned",
        source="comment",
    )
    ThreadLabel.objects.create(thread=thread, place=matched, is_subject=True)

    call_command("watch_extraction_report")

    out = capsys.readouterr().out
    post = _row(out, "post")
    comment = _row(out, "comment")
    assert post["precision"] == "1.00"
    assert post["recall"] == "1.00"
    assert post["tp"] == "1"
    assert comment["precision"] == "0.00"
    assert comment["fp"] == "1"
    assert comment["labels"] == "1"


@pytest.mark.django_db
def test_report_separates_threads_with_no_place(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), place)
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    no_place = ThreadFactory()
    ThreadLabel.objects.create(thread=no_place, place=None, is_useful=False)

    call_command("watch_extraction_report")

    out = capsys.readouterr().out
    assert "threads_with_no_place  1" in out
    row = _row(out, "body_pattern")
    assert row["labels"] == "1"
    assert row["tp"] == "1"


@pytest.mark.django_db
def test_report_json_output():
    thread = ThreadFactory()
    place = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), place)
    ThreadPlaceFactory(thread=thread, place=place, role="subject", source="post")
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    ThreadLabel.objects.create(thread=ThreadFactory(), place=None)

    stdout = StringIO()
    call_command("watch_extraction_report", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["labels"] == 1
    assert payload["threads_with_no_place"] == 1
    assert payload["methods"]["body_pattern"] == {
        "precision": 1.0,
        "recall": 1.0,
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "labels": 1,
    }
    assert payload["sources"]["post"]["tp"] == 1
    assert payload["sources"]["post"]["precision"] == 1.0


@pytest.mark.django_db
def test_report_since_filters_threads(capsys):
    old = ThreadFactory(first_seen=timezone.now() - timedelta(days=30))
    _mention_place(MentionFactory(thread=old), PlaceFactory())
    recent = ThreadFactory()
    ThreadLabel.objects.create(thread=recent, place=PlaceFactory(), is_subject=True)
    since = (timezone.localdate() - timedelta(days=1)).isoformat()

    call_command("watch_extraction_report", "--since", since)

    row = _row(capsys.readouterr().out, "body_pattern")
    assert row["tp"] == "0"
    assert row["fp"] == "0"
    assert row["fn"] == "1"
    assert row["labels"] == "1"


@pytest.mark.django_db
def test_report_rejects_bad_since():
    with pytest.raises(CommandError):
        call_command("watch_extraction_report", "--since", "yesterday")
