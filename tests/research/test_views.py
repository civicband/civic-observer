from urllib.parse import quote

import pytest
from django.urls import reverse

from research.browse import browse_set
from research.lens import Lens
from research.models import ResultSet, SetItem
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestProjectListView:
    def test_requires_login(self, client):
        response = client.get(reverse("research:project-list"))

        assert response.status_code == 302
        assert "/login" in response.url or "/stagedoor" in response.url

    def test_shows_only_the_signed_in_users_projects(self, client):
        user = UserFactory()
        other_user = UserFactory()
        ProjectFactory(owner=user, name="My Housing Project")
        ProjectFactory(owner=other_user, name="Someone Else's Project")

        client.force_login(user)
        response = client.get(reverse("research:project-list"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "My Housing Project" in content
        assert "Someone Else's Project" not in content

    def test_shows_set_count(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user, name="Housing")
        ResultSetFactory(project=project)

        client.force_login(user)
        response = client.get(reverse("research:project-list"))

        assert "1 set" in response.content.decode()


@pytest.mark.django_db
class TestProjectDetailView:
    def test_requires_login(self, client):
        project = ProjectFactory()

        response = client.get(reverse("research:project-detail", args=[project.id]))

        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=UserFactory())

        client.force_login(user)
        response = client.get(reverse("research:project-detail", args=[project.id]))

        assert response.status_code == 404

    def test_shows_project_sets(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user, name="Housing Project")
        ResultSetFactory(project=project, name="First snapshot")
        ResultSetFactory(
            project=project,
            name="Huge snapshot",
            status=ResultSet.Status.TOO_LARGE,
            item_count=600_000,
        )

        client.force_login(user)
        response = client.get(reverse("research:project-detail", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing Project" in content
        assert "First snapshot" in content
        assert "at least 600000 pages" in content


@pytest.mark.django_db
class TestSetCreateView:
    def test_requires_login(self, client):
        project = ProjectFactory()

        response = client.post(
            reverse("research:set-create", args=[project.id]),
            {"name": "Housing", "q": "housing"},
        )

        assert response.status_code == 302

    def test_cannot_create_in_another_users_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=UserFactory())

        client.force_login(user)
        response = client.post(
            reverse("research:set-create", args=[project.id]),
            {"name": "Housing", "q": "housing"},
        )

        assert response.status_code == 404
        assert not project.sets.exists()

    def test_creates_ready_set_from_form(self, client, settings):
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        user = UserFactory()
        project = ProjectFactory(owner=user)
        muni = MuniFactory(state="CA")
        document = MeetingDocumentFactory(
            municipality=muni,
            meeting_name="CityCouncil",
            document_type="agenda",
        )
        page = MeetingPageFactory(document=document, text="housing policy")

        client.force_login(user)
        response = client.post(
            reverse("research:set-create", args=[project.id]),
            {
                "name": "Housing",
                "q": "housing",
                "meeting_name": "CityCouncil",
                "states": "CA",
                "municipality": str(muni.id),
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
                "document_type": "agenda",
            },
        )

        result_set = project.sets.get()
        assert response.status_code == 302
        assert response.url == reverse(
            "research:set-detail", args=[project.id, result_set.id]
        )
        assert result_set.status == ResultSet.Status.READY
        assert result_set.item_count == 1
        assert result_set.items.get().page_id == page.id
        assert result_set.query["text"] == "housing"
        assert result_set.query["states"] == ["CA"]
        assert result_set.query["municipality_ids"] == [str(muni.id)]

    def test_ignores_invalid_municipality_and_date(self, client, settings):
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        user = UserFactory()
        project = ProjectFactory(owner=user)

        client.force_login(user)
        response = client.post(
            reverse("research:set-create", args=[project.id]),
            {
                "name": "Everything",
                "municipality": "not-a-uuid",
                "date_from": "not-a-date",
            },
        )

        assert response.status_code == 302
        result_set = project.sets.get()
        assert result_set.status == ResultSet.Status.READY
        assert result_set.query["municipality_ids"] == []
        assert result_set.query["date_from"] is None


@pytest.mark.django_db
class TestSetDetailView:
    def _ready_set(self, user=None):
        user = user or UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.READY)
        return user, project, result_set

    def test_requires_login(self, client):
        _user, project, result_set = self._ready_set()

        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        assert response.status_code == 302

    def test_cannot_view_another_users_set(self, client):
        user = UserFactory()
        _owner, project, result_set = self._ready_set()

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        assert response.status_code == 404

    def test_set_with_a_different_owned_project_returns_404(self, client):
        user, _project, result_set = self._ready_set()
        other_project = ProjectFactory(owner=user)

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[other_project.id, result_set.id])
        )

        assert response.status_code == 404

    def test_invalid_after_falls_back_to_first_page(self, client):
        user, project, result_set = self._ready_set()
        document = MeetingDocumentFactory(meeting_name="FallbackMeeting")
        SetItemFactory(
            result_set=result_set,
            page=MeetingPageFactory(document=document),
            rank=1.0,
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
            + "?after=not-a-cursor"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "FallbackMeeting" in content

    def test_renders_items_and_facets(self, client):
        user, project, result_set = self._ready_set()
        muni = MuniFactory(state="CA", name="Springfield")
        document = MeetingDocumentFactory(
            municipality=muni,
            meeting_name="CityCouncil",
            document_type="agenda",
        )
        page = MeetingPageFactory(document=document, page_number=7)
        SetItemFactory(result_set=result_set, page=page, rank=2.0)

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "CityCouncil" in content
        assert "Springfield" in content
        assert "Agenda" in content
        assert "Page 7" in content
        assert "States" in content
        assert "CA" in content

    def test_orphan_page_is_retained(self, client):
        user, project, result_set = self._ready_set()
        orphan_document = MeetingDocumentFactory(meeting_name="OrphanMeeting")
        orphan_page = MeetingPageFactory(
            document=orphan_document, text="orphan body text"
        )
        orphan = SetItemFactory(result_set=result_set, page=orphan_page, rank=2.0)
        SetItem.objects.filter(pk=orphan.pk).update(
            page_id="missing-page", state="CA", document_type="agenda"
        )
        live_document = MeetingDocumentFactory(meeting_name="LiveMeeting")
        SetItemFactory(
            result_set=result_set,
            page=MeetingPageFactory(document=live_document, text="live body text"),
            rank=1.0,
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Page no longer in corpus" in content
        assert "LiveMeeting" in content
        assert "live body text" in content
        assert "OrphanMeeting" not in content
        assert "orphan body text" not in content

    def test_visible_items_and_facets_agree_with_item_count(self, client):
        user, project, result_set = self._ready_set()
        muni = MuniFactory(state="CA", name="Springfield")
        document = MeetingDocumentFactory(municipality=muni, document_type="agenda")
        SetItemFactory(
            result_set=result_set,
            page=MeetingPageFactory(document=document),
            rank=2.0,
            state="CA",
            document_type="agenda",
        )
        orphan = SetItemFactory(result_set=result_set, rank=1.0)
        SetItem.objects.filter(pk=orphan.pk).update(
            page_id="missing-page", state="OR", document_type="minutes"
        )
        result_set.item_count = 2
        result_set.save(update_fields=["item_count", "modified"])

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Springfield" in content
        assert "Page no longer in corpus" in content

        items = response.context["items"]
        facets = response.context["facets"]
        assert len(items) == result_set.item_count == 2
        assert any(item.page_obj is None for item in items)
        assert sum(row["count"] for row in facets["states"]) == result_set.item_count
        assert (
            sum(row["count"] for row in facets["document_types"])
            == result_set.item_count
        )

    def test_too_large_count_is_approximate(self, client):
        user, project, result_set = self._ready_set()
        result_set.status = ResultSet.Status.TOO_LARGE
        result_set.item_count = 600_000
        result_set.error = "Too many matches"
        result_set.save()

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "at least 600000 pages" in content

    def test_keyset_pagination_with_after(self, client):
        user, project, result_set = self._ready_set()
        first_document = MeetingDocumentFactory(meeting_name="FirstMeeting")
        second_document = MeetingDocumentFactory(meeting_name="SecondMeeting")
        first = SetItemFactory(
            result_set=result_set,
            page=MeetingPageFactory(document=first_document),
            rank=3.0,
        )
        SetItemFactory(
            result_set=result_set,
            page=MeetingPageFactory(document=second_document),
            rank=2.0,
        )
        _first_page, after = browse_set(result_set, Lens(), limit=1)
        assert after is not None

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
            + f"?after={quote(after)}"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "SecondMeeting" in content
        assert "FirstMeeting" not in content
        assert first.page_id not in content

    def test_next_link_preserves_lens_querystring(self, client):
        user, project, result_set = self._ready_set()
        muni = MuniFactory(state="CA")
        document = MeetingDocumentFactory(municipality=muni)
        for rank in range(51, 0, -1):
            SetItemFactory(
                result_set=result_set,
                page=MeetingPageFactory(document=document),
                rank=float(rank),
            )

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
            + "?state=CA"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Next" in content
        assert "?state=CA&after=" in content

    def test_pending_set_renders_polling_status(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.PENDING)

        client.force_login(user)
        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert 'hx-trigger="every 2s"' in content


@pytest.mark.django_db
class TestSetStatusView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)

        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        assert response.status_code == 302

    def test_cannot_view_another_users_status(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=UserFactory())
        result_set = ResultSetFactory(project=project)

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        assert response.status_code == 404

    def test_pending_renders_polling_markup(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.PENDING)

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert 'hx-get="' in content
        assert 'hx-trigger="every 2s"' in content
        assert 'hx-swap="outerHTML"' in content
        assert "Pending" in content
        assert "HX-Refresh" not in response

    def test_ready_requests_full_page_refresh(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(
            project=project, status=ResultSet.Status.READY, item_count=4
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"

    def test_ready_link_preserves_lens(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(
            project=project, status=ResultSet.Status.READY, item_count=4
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
            + "?state=CA"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "?state=CA" in content

    def test_too_large_shows_error_and_narrowing_hint(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(
            project=project,
            status=ResultSet.Status.TOO_LARGE,
            error="Too many matches",
            item_count=600_000,
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Too many matches" in content
        assert "narrow" in content.lower()
        assert 'hx-trigger="every 2s"' not in content

    def test_ready_renders_no_polling(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(
            project=project, status=ResultSet.Status.READY, item_count=4
        )

        client.force_login(user)
        response = client.get(
            reverse("research:set-status", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Ready" in content
        assert 'hx-trigger="every 2s"' not in content
