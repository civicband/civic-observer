import pytest
from django.urls import reverse

from research.activity import (
    ActivityContext,
    activity_summary,
    build_feed,
    describe_event,
)
from tests.factories import (
    AuditEventFactory,
    CodeFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _ctx(project, events=None):
    return ActivityContext.load(project, events or [])


@pytest.mark.django_db
class TestDescribeEvent:
    def test_items_disposed_links_to_set(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Housing")
        event = AuditEventFactory(
            project=project,
            result_set=result_set,
            verb="items.disposed",
            payload={"item_ids": [], "from": {"u": 3}, "to": "i", "count": 3},
        )
        presented = describe_event(event, _ctx(project))
        assert presented["group"] == "review"
        assert "Ignored" in presented["title"]
        hrefs = [c["href"] for c in presented["counts"]]
        assert any("/sets/" in href for href in hrefs)
        assert any("disposition=i" in href for href in hrefs)

    def test_items_disposed_single_page_link(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory()
        item = SetItemFactory(result_set=result_set, page=page)
        event = AuditEventFactory(
            project=project,
            result_set=result_set,
            verb="items.disposed",
            payload={"item_ids": [item.id], "from": {"u": 1}, "to": "r", "count": 1},
        )
        presented = describe_event(event, _ctx(project, [event]))
        # Page's civic band url is empty in this factory, so no page link; but the
        # set link is present.
        assert any("/sets/" in c["href"] for c in presented["counts"])

    def test_rule_applied_resolves_set_names(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        event = AuditEventFactory(
            project=project,
            verb="rule.applied",
            payload={"predicate": {}, "counts": {str(result_set.id): 5}, "count": 5},
        )
        presented = describe_event(event, _ctx(project))
        assert presented["group"] == "rules"
        assert presented["counts"]
        assert presented["counts"][0]["href"]

    def test_items_coded_links_to_project_pages(self):
        project = ProjectFactory()
        code = CodeFactory(project=project)
        event = AuditEventFactory(
            project=project,
            verb="items.coded",
            payload={"code": str(code.id), "count": 4},
        )
        presented = describe_event(event, _ctx(project))
        assert presented["group"] == "codes"
        assert any("/pages/" in link["href"] for link in presented["links"])

    def test_unknown_verb_falls_back(self):
        project = ProjectFactory()
        event = AuditEventFactory(project=project, verb="something.weird")
        presented = describe_event(event, _ctx(project))
        assert presented["group"] == "other"
        assert "Something weird" in presented["title"]


@pytest.mark.django_db
class TestFeedAndSummary:
    def test_build_feed_merges_memos_and_events(self):
        project = ProjectFactory()
        project.memos.create(body="note")
        AuditEventFactory(project=project, verb="set.materialized")
        feed = build_feed(project)
        kinds = [entry["kind"] for entry in feed["entries"]]
        assert "memo" in kinds
        assert "event" in kinds

    def test_group_filter(self):
        project = ProjectFactory()
        AuditEventFactory(project=project, verb="set.materialized")
        AuditEventFactory(project=project, verb="rule.applied", payload={"counts": {}})
        feed = build_feed(project, group="rules")
        assert [e["event"].verb for e in feed["entries"]] == ["rule.applied"]

    def test_summary_counts(self):
        project = ProjectFactory()
        AuditEventFactory(project=project, verb="set.materialized")
        AuditEventFactory(project=project, verb="rule.applied", payload={"counts": {}})
        summary = activity_summary(project)
        assert summary["by_group"]["sets"] == 1
        assert summary["by_group"]["rules"] == 1


@pytest.mark.django_db
class TestTimelineView:
    def test_group_filter_and_links(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, name="Housing set")
        AuditEventFactory(
            project=project,
            result_set=result_set,
            verb="set.materialized",
            payload={"item_count": 10},
        )

        response = client.get(
            reverse("research:project-timeline", args=[project.id]) + "?group=sets"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Built set" in content
        assert "Open set" in content

    def test_timeline_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:project-timeline", args=[project.id]))
        assert response.status_code == 302
