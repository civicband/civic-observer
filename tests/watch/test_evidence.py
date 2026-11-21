"""Tests for subreddit place suggestions drawn from subject thread evidence."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.evidence import suggest_subreddits
from watch.models import Subreddit


def _thread(subreddit: Subreddit, *, days_ago: int = 0):
    """Create a thread last seen ``days_ago`` days ago."""
    return ThreadFactory(
        subreddit=subreddit,
        last_seen=timezone.now() - timedelta(days=days_ago),
    )


def _subject(subreddit: Subreddit, place, *, days_ago: int = 0):
    """Create one subject ThreadPlace for ``subreddit``."""
    return ThreadPlaceFactory(
        thread=_thread(subreddit, days_ago=days_ago),
        place=place,
        role="subject",
        source="subreddit",
    )


def _run(*args: str) -> str:
    stdout = StringIO()
    call_command("suggest_watch_subreddits", *args, stdout=stdout)
    return stdout.getvalue()


@pytest.mark.django_db
def test_suggests_place_from_subject_evidence():
    subreddit = SubredditFactory(name="localnews", source="gazetteer")
    subreddit.reviewed_at = timezone.now()
    subreddit.save(update_fields=["reviewed_at"])
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats == {"suggested": 1, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.kind == Subreddit.Kind.PLACE
    assert subreddit.source == Subreddit.Source.TEXT_EVIDENCE
    assert subreddit.confidence == Subreddit.Confidence.MEDIUM
    assert subreddit.reviewed_at is None
    assert subreddit.note == "text evidence: 4 of 4 subject threads"


@pytest.mark.django_db
def test_manual_place_conflict_is_counted_and_not_written():
    other = PlaceFactory(name="Other")
    subreddit = SubredditFactory(name="localnews", source="manual", place=other)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats == {"suggested": 0, "conflicts": 1, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place == other
    assert subreddit.source == Subreddit.Source.MANUAL
    assert subreddit.note == ""


@pytest.mark.django_db
def test_muted_subreddit_is_skipped_entirely():
    subreddit = SubredditFactory(name="muted", muted=True, place=None)
    manual = SubredditFactory(
        name="mutedmanual", muted=True, source="manual", place=PlaceFactory()
    )
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)
        _subject(manual, place)

    stats = suggest_subreddits()

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place is None
    subreddit.refresh_from_db()
    assert subreddit.source != Subreddit.Source.TEXT_EVIDENCE


@pytest.mark.django_db
def test_blank_note_is_replaced_with_evidence_note():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", note="")
    place = PlaceFactory(name="Chico")
    for _ in range(3):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats["suggested"] == 1
    subreddit.refresh_from_db()
    assert subreddit.note == "text evidence: 3 of 3 subject threads"


@pytest.mark.django_db
@pytest.mark.parametrize("note", ["operator: looking into it", "   "])
def test_existing_note_is_preserved(note):
    subreddit = SubredditFactory(name="localnews", source="gazetteer", note=note)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats["suggested"] == 1
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.note == note


@pytest.mark.django_db
def test_tied_top_places_produce_no_suggestion():
    subreddit = SubredditFactory(name="swing", source="gazetteer", place=None)
    first = PlaceFactory(name="Chico")
    second = PlaceFactory(name="Oroville")
    for _ in range(3):
        _subject(subreddit, first)
        _subject(subreddit, second)

    stats = suggest_subreddits(min_threads=3, share=0.5)

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 1}
    subreddit.refresh_from_db()
    assert subreddit.place is None


@pytest.mark.django_db
def test_dry_run_counts_without_writing():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits(dry_run=True)

    assert stats == {"suggested": 1, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place is None
    assert subreddit.source == Subreddit.Source.GAZETTEER
    assert subreddit.note == ""


@pytest.mark.django_db
def test_non_manual_disagreeing_place_is_rewritten():
    subreddit = SubredditFactory(
        name="localnews", source="gazetteer", place=PlaceFactory(name="Other")
    )
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats["suggested"] == 1
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.source == Subreddit.Source.TEXT_EVIDENCE


@pytest.mark.django_db
def test_agreeing_place_is_left_alone():
    place = PlaceFactory(name="Chico")
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=place)
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 1}
    subreddit.refresh_from_db()
    assert subreddit.source == Subreddit.Source.GAZETTEER


@pytest.mark.django_db
def test_threads_outside_the_window_are_ignored():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place, days_ago=100)

    stats = suggest_subreddits(days=90)

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place is None


@pytest.mark.django_db
def test_share_below_threshold_is_unchanged():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    other = PlaceFactory(name="Other")
    for _ in range(4):
        _subject(subreddit, place)
    for _ in range(6):
        _subject(subreddit, other)

    stats = suggest_subreddits(share=0.7)

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 1}
    subreddit.refresh_from_db()
    assert subreddit.place is None


@pytest.mark.django_db
def test_min_threads_below_threshold_is_unchanged():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(2):
        _subject(subreddit, place)

    stats = suggest_subreddits(min_threads=3)

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 1}
    subreddit.refresh_from_db()
    assert subreddit.place is None


@pytest.mark.django_db
def test_subreddit_without_evidence_is_not_counted():
    SubredditFactory(name="quiet", source="gazetteer", place=None)

    stats = suggest_subreddits()

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 0}


@pytest.mark.django_db
def test_distinct_threads_are_counted_once():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    other = PlaceFactory(name="Other")
    shared = _thread(subreddit)
    ThreadPlaceFactory(thread=shared, place=place, role="subject")
    ThreadPlaceFactory(thread=shared, place=other, role="mentioned")
    ThreadPlaceFactory(thread=_thread(subreddit), place=other, role="mentioned")

    stats = suggest_subreddits(min_threads=1, share=1.0)

    assert stats["suggested"] == 1
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.note == "text evidence: 1 of 1 subject threads"


@pytest.mark.django_db
def test_two_subject_places_in_one_thread_count_as_one_thread():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    other = PlaceFactory(name="Other")
    shared = _thread(subreddit)
    ThreadPlaceFactory(thread=shared, place=place, role="subject")
    ThreadPlaceFactory(thread=shared, place=other, role="subject")
    for _ in range(2):
        _subject(subreddit, place)

    stats = suggest_subreddits(min_threads=3, share=1.0)

    assert stats == {"suggested": 1, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.note == "text evidence: 3 of 3 subject threads"


@pytest.mark.django_db
def test_manual_row_without_place_is_not_a_conflict():
    subreddit = SubredditFactory(name="localnews", source="manual", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    stats = suggest_subreddits()

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place is None
    assert subreddit.source == Subreddit.Source.MANUAL
    assert subreddit.note == ""


@pytest.mark.django_db
def test_manual_disagreement_below_threshold_is_not_a_conflict():
    other = PlaceFactory(name="Other")
    subreddit = SubredditFactory(name="localnews", source="manual", place=other)
    place = PlaceFactory(name="Chico")
    for _ in range(2):
        _subject(subreddit, place)

    stats = suggest_subreddits(min_threads=3)

    assert stats == {"suggested": 0, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place == other


@pytest.mark.django_db
def test_subreddits_are_aggregated_independently():
    first = SubredditFactory(name="chico", source="gazetteer", place=None)
    second = SubredditFactory(name="oroville", source="gazetteer", place=None)
    chico = PlaceFactory(name="Chico")
    oroville = PlaceFactory(name="Oroville")
    for _ in range(4):
        _subject(first, chico)
    for _ in range(3):
        _subject(second, oroville)
    _subject(second, chico)

    stats = suggest_subreddits()

    assert stats == {"suggested": 2, "conflicts": 0, "unchanged": 0}
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.place == chico
    assert first.note == "text evidence: 4 of 4 subject threads"
    assert second.place == oroville
    assert second.note == "text evidence: 3 of 4 subject threads"


@pytest.mark.django_db
def test_share_boundary_is_inclusive():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    other = PlaceFactory(name="Other")
    for _ in range(7):
        _subject(subreddit, place)
    for _ in range(3):
        _subject(subreddit, other)

    stats = suggest_subreddits(share=0.7)

    assert stats == {"suggested": 1, "conflicts": 0, "unchanged": 0}
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.note == "text evidence: 7 of 10 subject threads"


@pytest.mark.django_db
def test_thread_last_seen_at_cutoff_is_included(monkeypatch):
    frozen = timezone.now()
    monkeypatch.setattr("watch.evidence.timezone.now", lambda: frozen)
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        ThreadPlaceFactory(
            thread=ThreadFactory(
                subreddit=subreddit, last_seen=frozen - timedelta(days=90)
            ),
            place=place,
            role="subject",
        )

    stats = suggest_subreddits(days=90)

    assert stats == {"suggested": 1, "conflicts": 0, "unchanged": 0}


@pytest.mark.django_db
def test_suggestion_refreshes_modified_and_place_geography():
    subreddit = SubredditFactory(
        name="ontario", source="gazetteer", place=None, state="", country=None
    )
    place = PlaceFactory(
        name="Toronto", state="ON", country="CA", place_type="canadian_city"
    )
    for _ in range(4):
        _subject(subreddit, place)
    stale = timezone.now() - timedelta(days=30)
    Subreddit.objects.filter(pk=subreddit.pk).update(modified=stale)

    stats = suggest_subreddits()

    assert stats["suggested"] == 1
    subreddit.refresh_from_db()
    assert subreddit.place == place
    assert subreddit.state == "ON"
    assert str(subreddit.country) == "CA"
    assert subreddit.modified > stale


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("note", "writes_note"), [("", True), ("operator: looking into it", False)]
)
def test_note_is_only_written_when_blank(monkeypatch, note, writes_note):
    subreddit = SubredditFactory(name="localnews", source="gazetteer", note=note)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)
    original_save = Subreddit.save
    saved_fields: list[str] = []

    def record_save(self, *args, **kwargs):
        saved_fields.extend(kwargs.get("update_fields") or [])
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Subreddit, "save", record_save)

    stats = suggest_subreddits()

    assert stats["suggested"] == 1
    assert ("note" in saved_fields) is writes_note
    subreddit.refresh_from_db()
    assert subreddit.place == place


@pytest.mark.django_db
def test_command_writes_counts_and_dry_run():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    output = _run("--dry-run")

    assert output.startswith("dry run: ")
    assert "suggested=1" in output
    assert "conflicts=0" in output
    assert "unchanged=0" in output
    subreddit.refresh_from_db()
    assert subreddit.place is None


@pytest.mark.django_db
def test_command_without_dry_run_writes_suggestion():
    subreddit = SubredditFactory(name="localnews", source="gazetteer", place=None)
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        _subject(subreddit, place)

    output = _run()

    assert not output.startswith("dry run: ")
    assert "suggested=1" in output
    subreddit.refresh_from_db()
    assert subreddit.place == place


@pytest.mark.django_db
@pytest.mark.parametrize("args", [("--days", "0"), ("--days", "-1")])
def test_command_rejects_non_positive_days(args):
    with pytest.raises(CommandError, match="--days"):
        _run(*args)


@pytest.mark.django_db
@pytest.mark.parametrize("args", [("--min-threads", "0"), ("--min-threads", "-2")])
def test_command_rejects_non_positive_min_threads(args):
    with pytest.raises(CommandError, match="--min-threads"):
        _run(*args)


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["1.5", "-0.1", "1.01"])
def test_command_rejects_share_outside_unit_interval(value):
    with pytest.raises(CommandError, match="--share"):
        _run("--share", value)


@pytest.mark.django_db
def test_function_rejects_invalid_options():
    with pytest.raises(ValueError):
        suggest_subreddits(days=0)
    with pytest.raises(ValueError):
        suggest_subreddits(min_threads=0)
    with pytest.raises(ValueError):
        suggest_subreddits(share=1.5)
