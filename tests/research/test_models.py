import pytest
from django.db import IntegrityError, transaction

from meetings.models import MeetingPage
from research.models import (
    AuditEvent,
    IgnoreRule,
    Memo,
    ResultSet,
)
from tests.factories import (
    CodeFactory,
    CodingFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestProject:
    def test_owner_scoped(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        assert project.owner == user
        assert list(user.research_projects.all()) == [project]

    def test_defaults(self):
        project = ProjectFactory()
        assert project.description == ""
        assert project.is_archived is False


@pytest.mark.django_db
class TestResultSet:
    def test_defaults(self):
        result_set = ResultSetFactory()
        assert result_set.status == ResultSet.Status.PENDING
        assert result_set.origin == ResultSet.Origin.QUERY
        assert result_set.item_count == 0
        assert result_set.materialized_at is None
        assert result_set.parent is None

    def test_child_set_links_parent(self):
        parent = ResultSetFactory()
        child = ResultSetFactory(parent=parent, origin=ResultSet.Origin.LENS)
        assert child.parent == parent
        assert list(parent.children.all()) == [child]


@pytest.mark.django_db
class TestSetItem:
    def test_unique_page_per_set(self):
        item = SetItemFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SetItemFactory(result_set=item.result_set, page=item.page)

    def test_same_page_in_two_sets(self):
        first = SetItemFactory()
        second = SetItemFactory(page=first.page)
        assert second.page == first.page

    def test_page_deletion_keeps_item(self):
        item = SetItemFactory()
        page_id = item.page_id
        MeetingPage.objects.filter(pk=page_id).delete()
        item.refresh_from_db()
        assert item.page_id == page_id


@pytest.mark.django_db
class TestCode:
    def test_root_code_name_unique_per_project(self):
        existing = CodeFactory()
        assert existing.parent is None
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodeFactory(project=existing.project, name=existing.name)

    def test_same_name_under_different_parents(self):
        project = ProjectFactory()
        parent_a = CodeFactory(project=project, name="Housing")
        parent_b = CodeFactory(project=project, name="Budget")
        child_a = CodeFactory(project=project, parent=parent_a, name="Zoning")
        child_b = CodeFactory(project=project, parent=parent_b, name="Zoning")
        assert child_a.name == child_b.name


@pytest.mark.django_db
class TestCoding:
    def test_one_whole_page_coding_per_code_and_page(self):
        coding = CodingFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodingFactory(
                    project=coding.project, code=coding.code, page=coding.page
                )

    def test_span_must_be_ordered(self):
        coding = CodingFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodingFactory(
                    project=coding.project,
                    code=coding.code,
                    page=coding.page,
                    span_start=10,
                    span_end=10,
                )

    def test_defaults(self):
        coding = CodingFactory()
        assert coding.span_start is None
        assert coding.span_end is None
        assert coding.is_exemplar is False
        assert coding.page_hash == ""


@pytest.mark.django_db
class TestMemoAndAudit:
    def test_project_memo_has_no_target(self):
        memo = Memo.objects.create(project=ProjectFactory(), body="Journal")
        assert memo.result_set is None
        assert memo.code is None
        assert memo.page is None

    def test_event_is_appended(self):
        project = ProjectFactory()
        event = AuditEvent.objects.create(project=project, verb="set.materialized")
        assert event.created is not None
        assert list(project.events.all()) == [event]


@pytest.mark.django_db
class TestIgnoreRule:
    def test_defaults(self):
        rule = IgnoreRule.objects.create(
            project=ProjectFactory(),
            name="Boilerplate",
            predicate={"text": "roll call"},
        )
        assert rule.is_active is True
        assert rule.reason == ""
