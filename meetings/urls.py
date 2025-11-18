from django.urls import URLPattern, URLResolver, path

from . import views

app_name = "meetings"

urlpatterns: list[URLPattern | URLResolver] = [
    path("search/", views.MeetingSearchView.as_view(), name="meeting-search"),
    path(
        "search/results/",
        views.meeting_page_search_results,
        name="meeting-search-results",
    ),
]
