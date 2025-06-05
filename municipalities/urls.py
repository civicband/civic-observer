from django.urls import path
from neapolitan.views import Role

from .views import MuniCRUDView

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
]
