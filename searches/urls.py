from django.urls import path
from neapolitan.views import Role

from .views import (
    SavedSearchCreateView,
    SavedSearchCRUDView,
    SavedSearchEditView,
    municipality_search,
    save_search_from_params,
    saved_search_email_preview,
)

app_name = "searches"

urlpatterns = [
    path("", SavedSearchCRUDView.as_view(role=Role.LIST), name="savedsearch-list"),
    path(
        "create/",
        SavedSearchCreateView.as_view(),
        name="savedsearch-create",
    ),
    path(
        "<uuid:pk>/",
        SavedSearchCRUDView.as_view(role=Role.DETAIL),
        name="savedsearch-detail",
    ),
    path(
        "<uuid:pk>/update/",
        SavedSearchEditView.as_view(),
        name="savedsearch-update",
    ),
    path(
        "<uuid:pk>/delete/",
        SavedSearchCRUDView.as_view(role=Role.DELETE),
        name="savedsearch-delete",
    ),
    # HTMX endpoints
    path(
        "municipality-search/",
        municipality_search,
        name="municipality-search",
    ),
    path(
        "save-from-params/",
        save_search_from_params,
        name="save-search-from-params",
    ),
    # Email preview URLs (staff only)
    path(
        "<uuid:pk>/email-preview/",
        saved_search_email_preview,
        name="savedsearch-email-preview",
    ),
    path(
        "<uuid:pk>/email-preview/<str:format>/",
        saved_search_email_preview,
        name="savedsearch-email-preview-format",
    ),
]
