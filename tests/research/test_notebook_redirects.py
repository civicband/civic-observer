import pytest
from django.urls import reverse

from tests.factories import ProjectFactory, UserFactory


@pytest.mark.django_db
class TestNotebookRedirects:
    def test_list_redirects_to_research(self, client):
        response = client.get("/notebooks/")
        assert response.status_code == 301
        assert response.url == "/research/"

    def test_detail_redirects_to_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        client.force_login(user)

        response = client.get(f"/notebooks/{project.id}/")

        assert response.status_code == 301
        assert response.url == reverse("research:project-detail", args=[project.id])

    def test_redirect_destination_404s_for_non_owner(self, client):
        project = ProjectFactory()
        client.force_login(UserFactory())

        response = client.get(reverse("research:project-detail", args=[project.id]))
        assert response.status_code == 404
