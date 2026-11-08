import pytest
from django.urls import reverse

from research.models import Coding
from tests.factories import (
    CodeFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _setup():
    user = UserFactory()
    project = ProjectFactory(owner=user)
    result_set = ResultSetFactory(project=project, status="ready")
    ca = MeetingPageFactory(state="CA")
    or_ = MeetingPageFactory(state="OR")
    SetItemFactory(result_set=result_set, page=ca, state="CA")
    SetItemFactory(result_set=result_set, page=or_, state="OR")
    code = CodeFactory(project=project, name="Housing")
    return user, project, result_set, ca, or_, code


@pytest.mark.django_db
class TestBulkCodeApplication:
    def test_confirmation_shows_count_and_code(self, client):
        user, project, result_set, _ca, _or, code = _setup()
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA"},
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "1 item" in content

    def test_confirm_applies_code_to_the_lens(self, client):
        user, project, result_set, ca, or_, code = _setup()
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA", "confirm": "1"},
        )

        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"
        assert Coding.objects.filter(code=code, page=ca).exists()
        assert not Coding.objects.filter(code=code, page=or_).exists()
        event = project.events.get(verb="items.coded")
        assert event.payload["count"] == 1

    def test_rejects_a_code_from_another_project(self, client):
        user, project, result_set, _ca, _or, _code = _setup()
        foreign = CodeFactory(project=ProjectFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(foreign.id), "lens": "state=CA"},
        )

        assert response.status_code == 404

    def test_requires_ready_set(self, client):
        user, project, result_set, _ca, _or, code = _setup()
        result_set.status = "pending"
        result_set.save(update_fields=["status"])
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA"},
        )

        assert response.status_code == 409

    def test_requires_login(self, client):
        _user, project, result_set, _ca, _or, code = _setup()
        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id)},
        )
        assert response.status_code == 302
