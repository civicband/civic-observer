from datetime import UTC, datetime

import pytest
from django.urls import reverse

from research.methods import build_methods
from tests.factories import (
    AuditEventFactory,
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestBuildMethods:
    def test_includes_each_query_with_date_and_count(self):
        project = ProjectFactory(name="Housing project")
        AuditEventFactory(
            project=project,
            verb="set.materialized",
            payload={
                "query": {"text": "housing", "states": ["CA"]},
                "item_count": 42,
            },
        )

        markdown = build_methods(project)

        assert "# How we did this" in markdown
        assert "housing" in markdown
        assert "CA" in markdown
        assert "42" in markdown

    def test_includes_the_funnel_per_set(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot", item_count=10)
        SetItemFactory(result_set=result_set, disposition="r")
        SetItemFactory(result_set=result_set, disposition="i")
        SetItemFactory(result_set=result_set, disposition="u")

        markdown = build_methods(project)

        assert "Snapshot" in markdown
        assert "Relevant: 1" in markdown
        assert "Ignored by hand: 1" in markdown
        assert "Unreviewed: 1" in markdown

    def test_includes_rule_ignores_by_rule_name(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot", item_count=2)
        rule = project.ignore_rules.create(name="Boilerplate", predicate={"text": "x"})
        SetItemFactory(result_set=result_set, disposition="i", disposition_by_rule=rule)

        markdown = build_methods(project)

        assert "Ignored by rule Boilerplate: 1" in markdown

    def test_includes_the_codebook_with_counts_and_definitions(self):
        project = ProjectFactory()
        code = CodeFactory(
            project=project,
            name="Housing",
            definition="Housing policy",
            exclusion="Only mentioned in passing",
        )
        CodingFactory(project=project, code=code, page=MeetingPageFactory())

        markdown = build_methods(project)

        assert "Housing" in markdown
        assert "Housing policy" in markdown
        assert "Only mentioned in passing" in markdown
        assert "Codings: 1" in markdown

    def test_includes_last_new_match_check(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot")
        result_set.last_checked_at = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
        result_set.save(update_fields=["last_checked_at"])

        markdown = build_methods(project)

        assert "Last checked for new matches" in markdown


@pytest.mark.django_db
class TestMethodsView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:methods", args=[project.id]))
        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory()
        response = client.get(reverse("research:methods", args=[project.id]))
        assert response.status_code == 404

    def test_renders_the_methods_note(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user, name="Housing project")
        AuditEventFactory(
            project=project,
            verb="set.materialized",
            payload={"query": {"text": "housing"}, "item_count": 7},
        )

        response = client.get(reverse("research:methods", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "How we did this" in content
        assert "housing" in content
