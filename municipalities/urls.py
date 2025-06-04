from django.urls import path

from .views import MuniCRUDView

app_name = "munis"

urlpatterns = [
    path("", MuniCRUDView.as_view(role="list"), name="muni-list"),
    path("create/", MuniCRUDView.as_view(role="create"), name="muni-create"),
    path("<uuid:pk>/", MuniCRUDView.as_view(role="detail"), name="muni-detail"),
    path("<uuid:pk>/update/", MuniCRUDView.as_view(role="update"), name="muni-update"),
    path("<uuid:pk>/delete/", MuniCRUDView.as_view(role="delete"), name="muni-delete"),
]
