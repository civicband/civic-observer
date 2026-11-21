"""Report place-extraction precision and recall against operator labels."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watch.models import PlaceMention, ThreadLabel, ThreadPlace

# A prediction and a label share the same shape: (thread, place, is_subject).
Key = tuple[object, object, bool]
Metrics = dict[str, int | float]


def _ratio(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator``, or 0 when there is nothing to divide."""
    return numerator / denominator if denominator else 0.0


def _metrics(predictions: set[Key], labels: set[Key]) -> Metrics:
    """Score ``predictions`` against ``labels`` as a set of (thread, place) keys."""
    true_positives = len(predictions & labels)
    return {
        "precision": round(_ratio(true_positives, len(predictions)), 4),
        "recall": round(_ratio(true_positives, len(labels)), 4),
        "tp": true_positives,
        "fp": len(predictions - labels),
        "fn": len(labels - predictions),
        "labels": len(labels),
    }


class Command(BaseCommand):
    help = "Report place-extraction precision and recall against operator labels."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            metavar="YYYY-MM-DD",
            help="Only include threads first seen on or after this date.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit JSON instead of a plain-text table.",
        )

    def handle(self, *args, **options):
        since = self._parse_since(options.get("since"))

        mention_places = PlaceMention.objects.all()
        thread_places = ThreadPlace.objects.all()
        label_rows = ThreadLabel.objects.all()
        if since is not None:
            start = timezone.make_aware(datetime.combine(since, time.min))
            mention_places = mention_places.filter(
                mention__thread__first_seen__gte=start
            )
            thread_places = thread_places.filter(thread__first_seen__gte=start)
            label_rows = label_rows.filter(thread__first_seen__gte=start)

        labels: set[Key] = set(
            label_rows.filter(place__isnull=False).values_list(
                "thread_id", "place_id", "is_subject"
            )
        )
        threads_with_no_place = label_rows.filter(place__isnull=True).count()

        by_method: defaultdict[str, set[Key]] = defaultdict(set)
        for thread_id, place_id, is_subject, method in mention_places.values_list(
            "mention__thread_id", "place_id", "is_subject", "method"
        ):
            by_method[method].add((thread_id, place_id, is_subject))

        by_source: defaultdict[str, set[Key]] = defaultdict(set)
        for thread_id, place_id, role, source in thread_places.values_list(
            "thread_id", "place_id", "role", "source"
        ):
            by_source[source].add(
                (thread_id, place_id, role == ThreadPlace.Role.SUBJECT)
            )

        # Enumerate every known method and source so a layer that produced
        # nothing still shows up, with its missed labels counted as false
        # negatives.  Union with observed values so unexpected rows survive.
        methods = sorted(set(PlaceMention.Method.values) | set(by_method))
        sources = sorted(set(ThreadPlace.Source.values) | set(by_source))
        method_metrics = {
            method: _metrics(by_method.get(method, set()), labels) for method in methods
        }
        source_metrics = {
            source: _metrics(by_source.get(source, set()), labels) for source in sources
        }

        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "since": since.isoformat() if since else None,
                        "labels": len(labels),
                        "threads_with_no_place": threads_with_no_place,
                        "methods": method_metrics,
                        "sources": source_metrics,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        self._write_table("methods", method_metrics)
        self._write_table("sources", source_metrics)
        self.stdout.write(f"threads_with_no_place  {threads_with_no_place}")

    def _parse_since(self, raw: str | None) -> date | None:
        """Parse ``--since`` as an ISO date, rejecting malformed values."""
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError("--since must be a YYYY-MM-DD date") from exc

    def _write_table(self, title: str, rows: dict[str, Metrics]) -> None:
        """Write one metrics table with a header and a row per group."""
        self.stdout.write(title)
        self.stdout.write(
            f"{'group':<20}{'precision':>10}{'recall':>8}"
            f"{'tp':>4}{'fp':>4}{'fn':>4}{'labels':>8}"
        )
        for group, metrics in rows.items():
            self.stdout.write(
                f"{group:<20}{metrics['precision']:>10.2f}{metrics['recall']:>8.2f}"
                f"{metrics['tp']:>4}{metrics['fp']:>4}{metrics['fn']:>4}"
                f"{metrics['labels']:>8}"
            )
