"""URL routing for the staff-only watch pages."""

from django.urls import path

from watch.views import (
    BacklogView,
    PlaceCorrectionView,
    TriageActionView,
    TriageView,
)

app_name = "watch"

urlpatterns = [
    path("triage/", TriageView.as_view(), name="triage"),
    path(
        "triage/<uuid:thread_id>/action/",
        TriageActionView.as_view(),
        name="action",
    ),
    path(
        "triage/<uuid:thread_id>/place/",
        PlaceCorrectionView.as_view(),
        name="place-correct",
    ),
    path("backlog/", BacklogView.as_view(), name="backlog"),
]
