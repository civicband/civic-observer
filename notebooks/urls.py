from django.urls import path

from . import views

app_name = "notebooks"

urlpatterns = [
    path("", views.NotebookListView.as_view(), name="notebook-list"),
]
