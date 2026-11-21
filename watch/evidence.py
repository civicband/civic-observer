"""Suggest subreddit places from accumulated subject thread evidence.

Milestone 5 records each thread's subject places.  This module rolls those
rows up per subreddit and proposes a place when a single place dominates the
subreddit's recent subject threads.  Suggestions are deliberately conservative:

* muted subreddits are ignored entirely;
* operator (``manual``) labels are never overwritten, only reported as
  ``conflicts`` when they disagree with the evidence;
* a tie between the two most-mentioned places produces no suggestion;
* an existing note is preserved, because it usually carries operator context.

Rows are counted by distinct thread, not by ``ThreadPlace`` row, so a thread
mentioning several places is still one data point.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from watch.models import Place, Subreddit, ThreadPlace

#: Default lookback window, in days.
DEFAULT_DAYS = 90
#: Default minimum number of threads behind the leading place.
DEFAULT_MIN_THREADS = 3
#: Default minimum share of a subreddit's subject threads.
DEFAULT_SHARE = 0.7


def _validate(*, days: int, min_threads: int, share: float) -> None:
    """Reject option values that cannot describe a suggestion."""
    if days < 1:
        raise ValueError("days must be a positive integer")
    if min_threads < 1:
        raise ValueError("min_threads must be a positive integer")
    if not 0 <= share <= 1:
        raise ValueError("share must be between 0 and 1")


def suggest_subreddits(
    *,
    days: int = DEFAULT_DAYS,
    min_threads: int = DEFAULT_MIN_THREADS,
    share: float = DEFAULT_SHARE,
    dry_run: bool = False,
) -> dict[str, int]:
    """Write text-evidence place suggestions and return the tally.

    Returns counts of ``suggested`` (written, or would be written when
    ``dry_run``), ``conflicts`` (manual rows that disagree with the evidence),
    and ``unchanged`` (considered rows that yielded no write or conflict).
    A row is considered only when it has subject evidence in the window.
    """
    _validate(days=days, min_threads=min_threads, share=share)

    cutoff = timezone.now() - timedelta(days=days)
    subject_places = ThreadPlace.objects.filter(
        role=ThreadPlace.Role.SUBJECT,
        thread__last_seen__gte=cutoff,
        thread__subreddit__isnull=False,
    )

    totals: dict[str, int] = {
        row["thread__subreddit"]: row["threads"]
        for row in subject_places.values("thread__subreddit").annotate(
            threads=Count("thread", distinct=True)
        )
    }

    by_place: defaultdict[str, dict[Any, int]] = defaultdict(dict)
    for row in subject_places.values("thread__subreddit", "place").annotate(
        threads=Count("thread", distinct=True)
    ):
        by_place[row["thread__subreddit"]][row["place"]] = row["threads"]

    stats = {"suggested": 0, "conflicts": 0, "unchanged": 0}

    for subreddit in Subreddit.objects.filter(pk__in=totals, muted=False):
        counts = by_place[subreddit.pk]
        total = totals[subreddit.pk]
        top_count = max(counts.values())
        leaders = [place for place, count in counts.items() if count == top_count]
        # Multiple leaders mean equally strong evidence for different places;
        # picking one would be a guess, so make no suggestion.
        if len(leaders) != 1 or top_count < min_threads or top_count / total < share:
            if subreddit.source != Subreddit.Source.MANUAL:
                stats["unchanged"] += 1
            continue

        place_id = leaders[0]
        if subreddit.source == Subreddit.Source.MANUAL:
            # A manual label that makes no place claim is not a disagreement:
            # only a conflicting place counts as a conflict.
            if subreddit.place_id is not None and subreddit.place_id != place_id:
                stats["conflicts"] += 1
            continue
        if subreddit.place_id == place_id:
            stats["unchanged"] += 1
            continue
        if dry_run:
            stats["suggested"] += 1
            continue

        wrote = _apply(subreddit, Place.objects.get(pk=place_id), top_count, total)
        if wrote:
            stats["suggested"] += 1
        else:
            # An operator changed the row between the read and the write.
            stats["unchanged"] += 1

    return stats


def _apply(
    subreddit: Subreddit, place: Place, thread_count: int, total_threads: int
) -> bool:
    """Write one suggestion, preserving operator state that is not ours.

    The row is re-read under a row lock so a manual label, mute, or note that
    landed after the caller's read is respected.  Returns whether the row was
    written; ``False`` means it became ineligible.
    """
    with transaction.atomic():
        current = Subreddit.objects.select_for_update().get(pk=subreddit.pk)
        if current.source == Subreddit.Source.MANUAL or current.muted:
            return False

        current.place = place
        current.kind = Subreddit.Kind.PLACE
        current.state = place.state
        current.country = place.country or None
        current.source = Subreddit.Source.TEXT_EVIDENCE
        current.confidence = Subreddit.Confidence.MEDIUM
        current.reviewed_at = None
        update_fields = [
            "place",
            "kind",
            "state",
            "country",
            "source",
            "confidence",
            "reviewed_at",
            "modified",
        ]
        # Only touch the note when it is ours to replace; otherwise an operator
        # may have edited it since this row was read.
        if not current.note:
            current.note = (
                f"text evidence: {thread_count} of {total_threads} subject threads"
            )
            update_fields.append("note")
        current.save(update_fields=update_fields)
    return True
