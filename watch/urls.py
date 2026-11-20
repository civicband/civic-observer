"""URL routing for the staff-only watch pages."""

from django.urls import path

from watch.views import BacklogView, TriageView

app_name = "watch"

urlpatterns = [
    path("triage/", TriageView.as_view(), name="triage"),
    path("backlog/", BacklogView.as_view(), name="backlog"),
]
