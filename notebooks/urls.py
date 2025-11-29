from django.urls import path

from . import views

app_name = "notebooks"

urlpatterns = [
    path("", views.NotebookListView.as_view(), name="notebook-list"),
    path("create/", views.NotebookCreateView.as_view(), name="notebook-create"),
    path("save-page/", views.SavePageView.as_view(), name="save-page"),
    path("<uuid:pk>/", views.NotebookDetailView.as_view(), name="notebook-detail"),
    path("<uuid:pk>/edit/", views.NotebookEditView.as_view(), name="notebook-edit"),
    path(
        "<uuid:pk>/archive/",
        views.NotebookArchiveView.as_view(),
        name="notebook-archive",
    ),
    path(
        "<uuid:pk>/delete/", views.NotebookDeleteView.as_view(), name="notebook-delete"
    ),
]
