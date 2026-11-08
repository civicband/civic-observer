import pytest
from django.urls import reverse

from research.models import Project, ResultSet
from research.projects import create_project
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    ProjectFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCreateProject:
    def test_creates_project_for_user(self):
        user = UserFactory()

        project = create_project(user, "Housing watch", "California")

        assert project.owner == user
        assert project.name == "Housing watch"
        assert project.description == "California"

    def test_requires_a_name(self):
        user = UserFactory()

        with pytest.raises(ValueError):
            create_project(user, "   ")


@pytest.mark.django_db
class TestSaveAsSetPanel:
    def test_requires_login(self, client):
        response = client.get(reverse("research:save-as-set"), {"query": "housing"})
        assert response.status_code == 302

    def test_shows_capped_count_and_projects(self, client):
        user = UserFactory()
        ProjectFactory(owner=user, name="Existing")
        MeetingPageFactory(
            document=MeetingDocumentFactory(),
            text="housing policy",
        )
        client.force_login(user)

        response = client.get(reverse("research:save-as-set"), {"query": "housing"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "Existing" in content
        assert "match" in content.lower()

    def test_passes_query_through_as_hidden_inputs(self, client):
        user = UserFactory()
        client.force_login(user)

        response = client.get(
            reverse("research:save-as-set"),
            {"query": "housing", "states": "CA,OR"},
        )

        content = response.content.decode()
        assert 'name="query" value="housing"' in content
        assert 'name="states" value="CA,OR"' in content

    def test_over_ceiling_hides_submit(self, client, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 0
        user = UserFactory()
        MeetingPageFactory(
            document=MeetingDocumentFactory(),
            text="housing policy",
        )
        client.force_login(user)

        response = client.get(reverse("research:save-as-set"), {"query": "housing"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "too many" in content.lower() or "narrow" in content.lower()
        assert 'type="submit"' not in content


@pytest.mark.django_db
class TestSaveAsSetView:
    def test_creates_set_in_existing_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        MeetingPageFactory(document=MeetingDocumentFactory(), text="housing policy")
        client.force_login(user)

        response = client.post(
            reverse("research:save-as-set"),
            {
                "project": str(project.id),
                "name": "Housing",
                "query": "housing",
            },
        )

        result_set = ResultSet.objects.get(project=project, name="Housing")
        assert response.status_code == 302
        assert response.url == reverse(
            "research:set-detail", args=[project.id, result_set.id]
        )

    def test_creates_project_when_new(self, client):
        user = UserFactory()
        MeetingPageFactory(document=MeetingDocumentFactory(), text="housing policy")
        client.force_login(user)

        response = client.post(
            reverse("research:save-as-set"),
            {"new_project": "Housing watch", "name": "Housing", "query": "housing"},
        )

        project = Project.objects.get(owner=user, name="Housing watch")
        assert ResultSet.objects.filter(project=project, name="Housing").exists()
        assert response.status_code == 302

    def test_cannot_target_another_users_project(self, client):
        user = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=other)
        client.force_login(user)

        response = client.post(
            reverse("research:save-as-set"),
            {"project": str(project.id), "name": "Housing", "query": "housing"},
        )

        assert response.status_code == 404
        assert not ResultSet.objects.filter(project=project).exists()
