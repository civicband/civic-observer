import pytest
from django.urls import reverse

from research.add_to_set import (
    add_page_to_default_set,
    add_page_to_set,
    default_set_for_user,
)
from research.models import Code, Coding, Memo, ResultSet, SetItem
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestDefaultSet:
    def test_uses_most_recently_modified_set(self):
        user = UserFactory()
        older = ResultSetFactory(project=ProjectFactory(owner=user))
        newer = ResultSetFactory(project=ProjectFactory(owner=user))

        assert default_set_for_user(user) == newer
        assert older != newer

    def test_creates_my_research_and_saved_pages_when_empty(self):
        user = UserFactory()

        result_set = default_set_for_user(user)

        assert result_set is not None
        assert result_set.name == "Saved pages"
        assert result_set.origin == ResultSet.Origin.MANUAL
        assert result_set.project.name == "My research"
        assert result_set.project.owner == user


@pytest.mark.django_db
class TestAddPageToSet:
    def test_adds_page_with_manual_added_via(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        item = add_page_to_set(project, result_set, page, actor=user)

        assert item.added_via == SetItem.AddedVia.MANUAL
        assert item.disposition == SetItem.Disposition.UNREVIEWED
        event = project.events.get(verb="item.added")
        assert event.payload["page"] == page.id

    def test_does_not_duplicate_page(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)

        add_page_to_set(project, result_set, page, actor=user)
        add_page_to_set(project, result_set, page, actor=user)

        assert SetItem.objects.filter(result_set=result_set, page=page).count() == 1
        assert project.events.filter(verb="item.added").count() == 0

    def test_code_application_records_events(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        add_page_to_set(
            project,
            result_set,
            page,
            actor=user,
            code_names=["Housing"],
        )

        assert project.events.filter(verb="code.created").exists()
        assert project.events.filter(verb="items.coded").exists()

    def test_rejects_a_long_code_name(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        with pytest.raises(ValueError):
            add_page_to_set(
                project,
                result_set,
                page,
                actor=user,
                code_names=["x" * 101],
            )

    def test_note_becomes_page_memo_and_codes_become_codings(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        add_page_to_set(
            project,
            result_set,
            page,
            actor=user,
            note="Follow up",
            code_names=["Housing", "Budget"],
        )

        assert Memo.objects.filter(
            project=project, page=page, body="Follow up"
        ).exists()
        for name in ("Housing", "Budget"):
            code = Code.objects.get(project=project, name=name)
            assert Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_rejects_set_from_another_project(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        other_set = ResultSetFactory(project=ProjectFactory(owner=user))
        page = MeetingPageFactory()

        with pytest.raises(ValueError):
            add_page_to_set(project, other_set, page, actor=user)


@pytest.mark.django_db
class TestAddPageToDefaultSet:
    def test_adds_to_created_default_set(self):
        user = UserFactory()
        page = MeetingPageFactory()

        item = add_page_to_default_set(user, page, actor=user)

        assert item.result_set.name == "Saved pages"
        assert item.result_set.project.name == "My research"


@pytest.mark.django_db
class TestAddToSetViews:
    def test_panel_requires_login(self, client):
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        response = client.get(reverse("research:add-to-set"), {"page": page.id})
        assert response.status_code == 302

    def test_panel_does_not_create_a_default_set_on_get(self, client):
        user = UserFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        client.get(reverse("research:add-to-set"), {"page": page.id})

        assert not ResultSet.objects.filter(project__owner=user).exists()

    def test_panel_lists_projects_and_sets(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, name="Saved pages")
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.get(reverse("research:add-to-set"), {"page": page.id})

        content = response.content.decode()
        assert response.status_code == 200
        assert "Saved pages" in content
        assert str(result_set.id) in content

    def test_post_adds_page(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": str(result_set.id), "note": "note"},
        )

        assert response.status_code == 200
        assert SetItem.objects.filter(result_set=result_set, page=page).exists()
        assert Memo.objects.filter(project=project, page=page, body="note").exists()

    def test_rejects_a_malformed_set_uuid(self, client):
        user = UserFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": "not-a-uuid"},
        )

        assert response.status_code == 400

    def test_rejects_a_long_code_name_with_400(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": str(result_set.id), "codes": ["x" * 101]},
        )

        assert response.status_code == 400
        assert not SetItem.objects.filter(page=page).exists()

    def test_cannot_add_to_another_users_set(self, client):
        user = UserFactory()
        result_set = ResultSetFactory(project=ProjectFactory())
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": str(result_set.id)},
        )

        assert response.status_code == 404
        assert not SetItem.objects.filter(page=page).exists()
