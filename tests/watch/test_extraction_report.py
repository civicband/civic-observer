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

SECTION_TITLES = ("methods", "sources", "overall")


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


def _section(out: str, title: str) -> list[str]:
    """Return the table lines under ``title``, excluding the header row."""
    lines = out.splitlines()
    try:
        start = lines.index(title) + 1
    except ValueError:
        raise AssertionError(f"missing section {title!r} in output") from None
    block = []
    for line in lines[start:]:
        if not line.strip() or line in SECTION_TITLES:
            break
        if line.startswith("threads_with_no_place"):
            break
        block.append(line)
    return block[1:]


def _row(out: str, section: str, group: str) -> dict[str, str]:
    """Parse the row whose first column is exactly ``group`` in ``section``."""
    for line in _section(out, section):
        columns = line.split()
        if columns[0] == group:
            return dict(
                zip(
                    ("group", "precision", "recall", "tp", "fp", "fn", "labels"),
                    columns,
                    strict=True,
                )
            )
    raise AssertionError(f"missing {group!r} row in {section!r} section")


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
    assert _row(out, "methods", "body_pattern")["tp"] == "1"


@pytest.mark.django_db
def test_report_counts_false_positives_and_negatives(capsys):
    thread = ThreadFactory()
    predicted = PlaceFactory()
    labelled = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), predicted)
    ThreadLabel.objects.create(thread=thread, place=labelled, is_subject=True)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "methods", "body_pattern")
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

    row = _row(capsys.readouterr().out, "methods", "body_pattern")
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
    post = _row(out, "sources", "post")
    comment = _row(out, "sources", "comment")
    assert post["precision"] == "1.00"
    assert post["recall"] == "1.00"
    assert post["tp"] == "1"
    assert comment["precision"] == "0.00"
    assert comment["fp"] == "1"
    assert comment["labels"] == "1"


@pytest.mark.django_db
def test_report_excludes_manual_rows(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), place, method="manual")
    ThreadPlaceFactory(thread=thread, place=place, role="subject", source="manual")
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)

    call_command("watch_extraction_report")

    out = capsys.readouterr().out
    method_groups = [line.split()[0] for line in _section(out, "methods")]
    source_groups = [line.split()[0] for line in _section(out, "sources")]
    assert "manual" not in method_groups
    assert "manual" not in source_groups
    # Manual data is ground truth, so it cannot score as a non-manual hit.
    assert _row(out, "methods", "body_pattern")["fn"] == "1"
    assert _row(out, "sources", "post")["fn"] == "1"


@pytest.mark.django_db
def test_report_lists_unused_group_with_missed_labels(capsys):
    ThreadLabel.objects.create(
        thread=ThreadFactory(), place=PlaceFactory(), is_subject=True
    )

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "methods", "llm")
    assert row["precision"] == "n/a"
    assert row["recall"] == "0.00"
    assert row["tp"] == "0"
    assert row["fp"] == "0"
    assert row["fn"] == "1"
    assert row["labels"] == "1"


@pytest.mark.django_db
def test_report_counts_mentioned_true_positive(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="mentioned", source="comment")
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=False)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "sources", "comment")
    assert row["tp"] == "1"
    assert row["precision"] == "1.00"
    assert row["recall"] == "1.00"


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
    row = _row(out, "methods", "body_pattern")
    assert row["labels"] == "1"
    assert row["tp"] == "1"


@pytest.mark.django_db
def test_report_counts_prediction_on_no_place_thread_as_false_positive(capsys):
    thread = ThreadFactory()
    ThreadLabel.objects.create(thread=thread, place=None)
    _mention_place(MentionFactory(thread=thread), PlaceFactory())

    call_command("watch_extraction_report")

    out = capsys.readouterr().out
    assert "threads_with_no_place  1" in out
    row = _row(out, "methods", "body_pattern")
    assert row["tp"] == "0"
    assert row["fp"] == "1"
    assert row["fn"] == "0"
    assert row["labels"] == "0"
    assert row["precision"] == "0.00"
    assert row["recall"] == "n/a"


@pytest.mark.django_db
def test_report_collapses_duplicate_predictions_across_mentions(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), place)
    _mention_place(MentionFactory(thread=thread), place)
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "methods", "body_pattern")
    assert row["tp"] == "1"
    assert row["fp"] == "0"
    assert row["fn"] == "0"
    assert row["precision"] == "1.00"


@pytest.mark.django_db
def test_report_overall_unions_non_manual_predictions(capsys):
    thread = ThreadFactory()
    matched = PlaceFactory()
    extra = PlaceFactory()
    _mention_place(MentionFactory(thread=thread), matched)
    ThreadPlaceFactory(thread=thread, place=extra, role="mentioned", source="post")
    ThreadLabel.objects.create(thread=thread, place=matched, is_subject=True)

    call_command("watch_extraction_report")

    row = _row(capsys.readouterr().out, "overall", "overall")
    assert row["tp"] == "1"
    assert row["fp"] == "1"
    assert row["fn"] == "0"
    assert row["labels"] == "1"
    assert row["precision"] == "0.50"
    assert row["recall"] == "1.00"


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
    assert payload["overall"]["tp"] == 1
    assert "manual" not in payload["methods"]
    assert "manual" not in payload["sources"]


@pytest.mark.django_db
def test_report_json_since_echoes_date():
    thread = ThreadFactory()
    _mention_place(MentionFactory(thread=thread), PlaceFactory())
    since = (timezone.localdate() - timedelta(days=1)).isoformat()

    stdout = StringIO()
    call_command("watch_extraction_report", "--json", "--since", since, stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["since"] == since


@pytest.mark.django_db
def test_report_since_filters_threads(capsys):
    old = ThreadFactory(first_seen=timezone.now() - timedelta(days=30))
    _mention_place(MentionFactory(thread=old), PlaceFactory())
    recent = ThreadFactory()
    ThreadLabel.objects.create(thread=recent, place=PlaceFactory(), is_subject=True)
    since = (timezone.localdate() - timedelta(days=1)).isoformat()

    call_command("watch_extraction_report", "--since", since)

    row = _row(capsys.readouterr().out, "methods", "body_pattern")
    assert row["tp"] == "0"
    assert row["fp"] == "0"
    assert row["fn"] == "1"
    assert row["labels"] == "1"


@pytest.mark.django_db
def test_report_empty_database_has_no_ratios(capsys):
    call_command("watch_extraction_report")

    out = capsys.readouterr().out
    assert "n/a" not in out
    assert "threads_with_no_place  0" in out
    assert _section(out, "methods") == []
    assert _section(out, "sources") == []
    assert "overall" not in out.splitlines()

    stdout = StringIO()
    call_command("watch_extraction_report", "--json", stdout=stdout)
    payload = json.loads(stdout.getvalue())
    assert payload["methods"] == {}
    assert payload["sources"] == {}
    assert payload["overall"] is None
    assert payload["labels"] == 0


@pytest.mark.django_db
def test_report_rejects_bad_since():
    with pytest.raises(CommandError):
        call_command("watch_extraction_report", "--since", "yesterday")
