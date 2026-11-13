import pytest
from django.urls import reverse

from research.models import Code, Coding
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
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
class TestCodebookView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:codebook", args=[project.id]))
        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        _login(client)
        project = ProjectFactory()
        response = client.get(reverse("research:codebook", args=[project.id]))
        assert response.status_code == 404

    def test_shows_tree_counts_and_missing_exclusion(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        parent = CodeFactory(project=project, name="Housing")
        CodeFactory(project=project, name="Housing policy", parent=parent)
        page = MeetingPageFactory()
        CodingFactory(project=project, code=parent, page=page)

        response = client.get(reverse("research:codebook", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "Housing policy" in content
        assert "No exclusion rule" in content
        assert "1 coding" in content

    def test_shows_exemplar_text(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")
        page = MeetingPageFactory(text="The council approved the housing plan.")
        CodingFactory(project=project, code=code, page=page, is_exemplar=True)

        response = client.get(reverse("research:codebook", args=[project.id]))

        assert "The council approved the housing plan." in response.content.decode()


@pytest.mark.django_db
class TestCodeMutations:
    def test_create_code_redirects_and_records_event(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)

        response = client.post(
            reverse("research:code-create", args=[project.id]),
            {"name": "Housing", "definition": "Housing policy"},
        )

        assert response.status_code == 302
        code = Code.objects.get(project=project, name="Housing")
        assert code.definition == "Housing policy"
        assert project.events.filter(verb="code.created").exists()

    def test_create_rejects_duplicate_with_400(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-create", args=[project.id]), {"name": "Housing"}
        )

        assert response.status_code == 400
        assert "already exists" in response.content.decode()

    def test_create_requires_post(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        response = client.get(reverse("research:code-create", args=[project.id]))
        assert response.status_code == 405

    def test_rename_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "Housing policy"},
        )

        assert response.status_code == 302
        code.refresh_from_db()
        assert code.name == "Housing policy"

    def test_cannot_rename_another_users_code(self, client):
        _login(client)
        project = ProjectFactory()
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "Hijacked"},
        )

        assert response.status_code == 404

    def test_archive_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-archive", args=[project.id, code.id])
        )

        assert response.status_code == 302
        code.refresh_from_db()
        assert code.is_archived is True

    def test_merge_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        source = CodeFactory(project=project, name="Housing")
        target = CodeFactory(project=project, name="Housing policy")

        response = client.post(
            reverse("research:code-merge", args=[project.id, source.id]),
            {"target": str(target.id)},
        )

        assert response.status_code == 302
        assert Code.objects.filter(pk=source.pk).exists() is False

    def test_merge_rejects_cross_project_target(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        source = CodeFactory(project=project, name="Housing")
        target = CodeFactory(project=ProjectFactory(), name="Other")

        response = client.post(
            reverse("research:code-merge", args=[project.id, source.id]),
            {"target": str(target.id)},
        )

        assert response.status_code == 404

    def test_rename_updates_definition_and_exclusion(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {
                "name": "Housing",
                "definition": "Housing policy",
                "inclusion": "When housing is discussed",
                "exclusion": "Only mentioned in passing",
            },
        )

        assert response.status_code == 302
        code.refresh_from_db()
        assert code.definition == "Housing policy"
        assert code.inclusion == "When housing is discussed"
        assert code.exclusion == "Only mentioned in passing"

    def test_rename_rejects_a_blank_name_with_400(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "   "},
        )

        assert response.status_code == 400

    def test_rename_rejects_a_duplicate_name_with_400(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        CodeFactory(project=project, name="Housing")
        code = CodeFactory(project=project, name="Housing policy")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "Housing"},
        )

        assert response.status_code == 400


@pytest.mark.django_db
class TestPageCodingPartial:
    def test_shows_codes_and_memos_for_the_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory(text="Housing plan")
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=page)
        project.memos.create(body="Worth revisiting", page=page)

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "Worth revisiting" in content

    def test_links_to_civic_band(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        content = response.content.decode()
        assert "View on CivicBand" in content
        assert "civic.band/meetings/" in content

    def test_apply_code_to_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:page-code", args=[project.id, page.id]),
            {"code": str(code.id), "action": "apply"},
        )

        assert response.status_code == 200
        assert Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_remove_code_from_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=page)

        response = client.post(
            reverse("research:page-code", args=[project.id, page.id]),
            {"code": str(code.id), "action": "remove"},
        )

        assert response.status_code == 200
        assert not Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_cannot_view_another_users_page_coding(self, client):
        _login(client)
        project = ProjectFactory()
        page = MeetingPageFactory()

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        assert response.status_code == 404

    def test_marks_and_unmarks_an_exemplar(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        coding = CodingFactory(project=project, code=code, page=page)
        url = reverse("research:code-exemplar", args=[project.id, code.id, coding.id])

        response = client.post(url, {"is_exemplar": "1"})

        assert response.status_code == 200
        coding.refresh_from_db()
        assert coding.is_exemplar is True

        response = client.post(url, {"is_exemplar": "0"})

        assert response.status_code == 200
        coding.refresh_from_db()
        assert coding.is_exemplar is False

    def test_cannot_mark_another_users_exemplar(self, client):
        _login(client)
        project = ProjectFactory()
        page = MeetingPageFactory()
        code = CodeFactory(project=project, name="Housing")
        coding = CodingFactory(project=project, code=code, page=page)

        response = client.post(
            reverse("research:code-exemplar", args=[project.id, code.id, coding.id]),
            {"is_exemplar": "1"},
        )

        assert response.status_code == 404

    def test_shows_text_changed_since_coded(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory(text="New text", content_hash="new-hash")
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(
            project=project,
            code=code,
            page=page,
            quote="Old text",
            page_hash="old-hash",
        )

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        content = response.content.decode()
        assert "Text changed since coded" in content
        assert "Old text" in content
