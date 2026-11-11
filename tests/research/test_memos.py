import pytest
from django.urls import reverse

from meetings.models import MeetingPage
from research.memos import create_memo, delete_memo, project_timeline, update_memo
from research.models import Memo
from tests.factories import (
    AuditEventFactory,
    CodeFactory,
    MeetingPageFactory,
    MemoFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _login(client):
    user = UserFactory()
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestMemoService:
    def test_creates_a_project_memo(self):
        project = ProjectFactory()
        author = UserFactory()

        memo = create_memo(project, author=author, body="  First hunch  ")

        assert memo.project == project
        assert memo.author == author
        assert memo.body == "First hunch"
        assert memo.result_set_id is None
        assert memo.code_id is None
        assert memo.page_id is None

    def test_rejects_more_than_one_target(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        code = CodeFactory(project=project)

        with pytest.raises(ValueError):
            create_memo(
                project,
                author=UserFactory(),
                body="Too many targets",
                result_set=result_set,
                code=code,
            )

    def test_rejects_a_set_from_another_project(self):
        project = ProjectFactory()
        foreign = ResultSetFactory(project=ProjectFactory())

        with pytest.raises(ValueError):
            create_memo(
                project, author=UserFactory(), body="Wrong project", result_set=foreign
            )

    def test_rejects_a_blank_body(self):
        project = ProjectFactory()

        with pytest.raises(ValueError):
            create_memo(project, author=UserFactory(), body="   ")

    def test_update_and_delete(self):
        project = ProjectFactory()
        memo = create_memo(project, author=UserFactory(), body="Old")

        update_memo(memo, body="New")
        memo.refresh_from_db()
        assert memo.body == "New"

        delete_memo(memo)
        assert Memo.objects.filter(pk=memo.pk).exists() is False

    def test_timeline_limit_returns_newest_entries(self):
        project = ProjectFactory()
        author = UserFactory()
        project.memos.create(body="older", author=author)
        newer = project.memos.create(body="newer", author=author)

        timeline = project_timeline(project, limit=1)

        assert [entry["memo"] for entry in timeline] == [newer]

    def test_timeline_interleaves_memos_and_events_by_date(self):
        project = ProjectFactory()
        older = create_memo(project, author=UserFactory(), body="Older memo")
        event = AuditEventFactory(project=project)
        newer = create_memo(project, author=UserFactory(), body="Newer memo")

        timeline = project_timeline(project)

        assert [entry["kind"] for entry in timeline] == ["memo", "event", "memo"]
        assert timeline[0]["memo"] == newer
        assert timeline[1]["event"] == event
        assert timeline[2]["memo"] == older


@pytest.mark.django_db
class TestMemoViews:
    def test_create_project_memo_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "A project note"},
        )

        assert response.status_code == 302
        assert project.memos.filter(body="A project note", author=user).exists()

    def test_create_set_memo_redirects_to_set(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Why this query", "result_set": str(result_set.id)},
        )

        assert response.status_code == 302
        assert response.url == reverse(
            "research:set-detail", args=[project.id, result_set.id]
        )
        assert result_set.memos.filter(body="Why this query").exists()

    def test_create_code_memo_redirects_to_codebook(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Meaning shifted", "code": str(code.id)},
        )

        assert response.status_code == 302
        assert response.url == reverse("research:codebook", args=[project.id])
        assert code.memos.filter(body="Meaning shifted").exists()

    def test_create_page_memo_requires_page_in_project(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Orphan page", "page": page.id},
        )

        assert response.status_code == 404

    def test_create_page_memo_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {
                "body": "Note on the page",
                "page": page.id,
                "next": reverse(
                    "research:set-detail", args=[project.id, result_set.id]
                ),
            },
        )

        assert response.status_code == 302
        assert project.memos.filter(body="Note on the page", page=page).exists()

    def test_create_page_memo_404s_when_page_is_deleted(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)
        page_id = page.id
        MeetingPage.objects.filter(pk=page_id).delete()

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Ghost page", "page": page_id},
        )

        assert response.status_code == 404

    def test_create_rejects_two_targets(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        code = CodeFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {
                "body": "Two targets",
                "result_set": str(result_set.id),
                "code": str(code.id),
            },
        )

        assert response.status_code == 400

    def test_cannot_create_on_another_users_project(self, client):
        _login(client)
        project = ProjectFactory()

        response = client.post(
            reverse("research:memo-create", args=[project.id]), {"body": "Nope"}
        )

        assert response.status_code == 404

    def test_update_and_delete_are_owner_scoped(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        memo = MemoFactory(project=project, body="Old")

        response = client.post(
            reverse("research:memo-update", args=[project.id, memo.id]),
            {"body": "New"},
        )
        assert response.status_code == 302
        memo.refresh_from_db()
        assert memo.body == "New"

        response = client.post(
            reverse("research:memo-delete", args=[project.id, memo.id])
        )
        assert response.status_code == 302
        assert Memo.objects.filter(pk=memo.pk).exists() is False

    def test_cannot_update_another_users_memo(self, client):
        _login(client)
        project = ProjectFactory()
        memo = MemoFactory(project=project, body="Old")

        response = client.post(
            reverse("research:memo-update", args=[project.id, memo.id]),
            {"body": "Hijacked"},
        )
        assert response.status_code == 404

    def test_project_timeline_page_shows_entries(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        project.memos.create(body="A project note", author=user)
        AuditEventFactory(project=project, verb="set.materialized")

        response = client.get(reverse("research:project-timeline", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "A project note" in content
        assert "set.materialized" in content

    def test_project_detail_links_to_timeline_without_rendering_it(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        project.memos.create(body="A project note", author=user)

        response = client.get(reverse("research:project-detail", args=[project.id]))

        content = response.content.decode()
        assert "A project note" not in content
        assert reverse("research:project-timeline", args=[project.id]) in content

    def test_page_coding_memo_form_has_next(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="next"' in content
        assert (
            reverse("research:set-detail", args=[project.id, result_set.id]) in content
        )

    def test_codebook_memo_form_has_next(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)

        response = client.get(reverse("research:codebook", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="next"' in content
        assert reverse("research:codebook", args=[project.id]) in content
