from django.urls import path

from . import views

app_name = "notebooks"

urlpatterns = [
    path("", views.NotebookListView.as_view(), name="notebook-list"),
    path("create/", views.NotebookCreateView.as_view(), name="notebook-create"),
    path("<uuid:pk>/", views.NotebookDetailView.as_view(), name="notebook-detail"),
]
