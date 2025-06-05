from django.urls import path
from neapolitan.views import Role

from .views import MuniCRUDView, MuniWebhookUpdateView

app_name = "munis"

urlpatterns = [
    path("", MuniCRUDView.as_view(role=Role.LIST), name="muni-list"),
    path("create/", MuniCRUDView.as_view(role=Role.CREATE), name="muni-create"),
    path("<uuid:pk>/", MuniCRUDView.as_view(role=Role.DETAIL), name="muni-detail"),
    path(
        "<uuid:pk>/update/", MuniCRUDView.as_view(role=Role.UPDATE), name="muni-update"
    ),
    path(
        "<uuid:pk>/delete/", MuniCRUDView.as_view(role=Role.DELETE), name="muni-delete"
    ),
    # Webhook API endpoint for updating/creating municipalities by subdomain
    path(
        "api/update/<str:subdomain>/",
        MuniWebhookUpdateView.as_view(),
        name="muni-webhook-update",
    ),
]
