import pytest
from django.urls import reverse

from research.lens import Lens
from research.models import SetItem
from research.project_pages import (
    browse_project_pages,
    project_page_counts,
    project_page_facets,
)
from tests.factories import (
    CodingFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _ready_set(project):
    return ResultSetFactory(project=project, status="ready")


@pytest.mark.django_db
class TestProjectPagesService:
    def test_distinct_pages_with_disposition_precedence(self):
        project = ProjectFactory()
        first = _ready_set(project)
        second = _ready_set(project)
        page_a = SetItemFactory(result_set=first, disposition="i").page
        SetItemFactory(result_set=second, page=page_a, disposition="r")
        SetItemFactory(result_set=first, disposition="u")

        rows, _ = browse_project_pages(project, Lens(dispositions=("r", "i", "u")))

        assert len(rows) == 2
        by_id = {row.page.id: row for row in rows}
        # Precedence: relevant beats ignored.
        assert by_id[page_a.id].disposition == SetItem.Disposition.RELEVANT
        assert len(by_id[page_a.id].sets) == 2

    def test_disposition_filter_uses_derived_value(self):
        project = ProjectFactory()
        result_set = _ready_set(project)
        SetItemFactory(result_set=result_set, disposition="i")
        SetItemFactory(result_set=result_set, disposition="u")

        ignored, _ = browse_project_pages(project, Lens(dispositions=("i",)))
        assert len(ignored) == 1
        assert ignored[0].disposition == SetItem.Disposition.IGNORED

    def test_code_filter(self):
        project = ProjectFactory()
        result_set = _ready_set(project)
        coded = SetItemFactory(result_set=result_set, disposition="r")
        SetItemFactory(result_set=result_set, disposition="r")
        code = CodingFactory(project=project, page=coded.page).code

        rows, _ = browse_project_pages(
            project, Lens(dispositions=("r",), codes_any=(code.id,))
        )
        assert [row.page.id for row in rows] == [coded.page.id]

    def test_ignores_non_ready_sets(self):
        project = ProjectFactory()
        pending = ResultSetFactory(project=project, status="pending")
        SetItemFactory(result_set=pending, disposition="r")
        rows, _ = browse_project_pages(project, Lens(dispositions=("r",)))
        assert rows == []

    def test_counts(self):
        project = ProjectFactory()
        result_set = _ready_set(project)
        SetItemFactory(result_set=result_set, disposition="r")
        SetItemFactory(result_set=result_set, disposition="i")
        SetItemFactory(result_set=result_set, disposition="u")

        counts = project_page_counts(project)
        assert counts["r"] == 1
        assert counts["i"] == 1
        assert counts["u"] == 1
        assert counts["total"] == 3

    def test_facets_agree_with_rows(self):
        project = ProjectFactory()
        result_set = _ready_set(project)
        SetItemFactory(result_set=result_set, disposition="r", state="CA")
        SetItemFactory(result_set=result_set, disposition="i", state="OR")

        facets = project_page_facets(project, Lens(dispositions=("r", "i", "u")))
        total = sum(row["count"] for row in facets["disposition"])
        assert total == 2


@pytest.mark.django_db
class TestProjectPagesView:
    def _login(self, client):
        user = UserFactory()
        client.force_login(user)
        return user

    def test_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:project-pages", args=[project.id]))
        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        self._login(client)
        project = ProjectFactory()
        response = client.get(reverse("research:project-pages", args=[project.id]))
        assert response.status_code == 404

    def test_lists_pages(self, client):
        user = self._login(client)
        project = ProjectFactory(owner=user)
        result_set = _ready_set(project)
        SetItemFactory(result_set=result_set, disposition="r")

        response = client.get(
            reverse("research:project-pages", args=[project.id]) + "?disposition=r"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Pages" in content
        assert "Relevant (1)" in content

    def test_ignored_link(self, client):
        user = self._login(client)
        project = ProjectFactory(owner=user)
        result_set = _ready_set(project)
        SetItemFactory(result_set=result_set, disposition="i")

        response = client.get(
            reverse("research:project-pages", args=[project.id]) + "?disposition=i"
        )
        assert response.status_code == 200
        assert response.context["rows"]
