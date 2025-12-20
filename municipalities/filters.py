from datetime import timedelta

import django_filters
from django.utils import timezone

from .models import Muni


class ActivityFilter(django_filters.ChoiceFilter):
    """Filter by last_updated within N days."""

    def __init__(self, *args, **kwargs):
        kwargs["choices"] = [
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
            ("90", "Last 90 days"),
        ]
        kwargs["empty_label"] = "Any time"
        super().__init__(*args, **kwargs)

    def filter(self, qs, value):
        if not value:
            return qs
        days = int(value)
        cutoff = timezone.now() - timedelta(days=days)
        return qs.filter(last_updated__gte=cutoff)


class MuniFilter(django_filters.FilterSet):
    state = django_filters.CharFilter(field_name="state", lookup_expr="exact")
    kind = django_filters.CharFilter(field_name="kind", lookup_expr="exact")
    activity = ActivityFilter(field_name="last_updated")

    class Meta:
        model = Muni
        fields = ["state", "kind", "activity"]
