import uuid

import pytest
from django.http import QueryDict
from django.urls import reverse

from research.lenses import (
    active_lens,
    delete_lens,
    lenses_for,
    promote_lens,
    save_lens,
    update_lens,
)
from research.models import Lens as LensModel
from research.models import ResultSet
from tests.factories import (
    LensFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _lens(**kwargs):
    from research.lens import Lens

    return Lens(**kwargs)


@pytest.mark.django_db
class TestLensServices:
    def test_save_set_scoped_lens(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        lens_obj = save_lens(
            project,
            name="Relevant housing",
            lens=_lens(dispositions=("r",)),
            result_set=result_set,
        )
        assert lens_obj.result_set_id == result_set.id
        assert lens_obj.is_project_scope is False
        assert lenses_for(project, result_set=result_set) == [lens_obj]

    def test_save_project_scoped_lens(self):
        project = ProjectFactory()
        lens_obj = save_lens(
            project, name="All ignored", lens=_lens(dispositions=("i",))
        )
        assert lens_obj.is_project_scope is True
        assert lenses_for(project, result_set=None) == [lens_obj]

    def test_duplicate_name_rejected(self):
        project = ProjectFactory()
        save_lens(project, name="Dup", lens=_lens())
        with pytest.raises(ValueError):
            save_lens(project, name="Dup", lens=_lens())

    def test_blank_name_rejected(self):
        project = ProjectFactory()
        with pytest.raises(ValueError):
            save_lens(project, name="   ", lens=_lens())

    def test_set_from_another_project_rejected(self):
        project = ProjectFactory()
        other = ResultSetFactory()
        with pytest.raises(ValueError):
            save_lens(project, name="x", lens=_lens(), result_set=other)

    def test_update_lens_renames(self):
        lens_obj = LensFactory(name="Old")
        update_lens(lens_obj, name="New")
        lens_obj.refresh_from_db()
        assert lens_obj.name == "New"

    def test_delete_lens(self):
        lens_obj = LensFactory()
        delete_lens(lens_obj)
        assert not LensModel.objects.filter(pk=lens_obj.pk).exists()

    def test_promote_set_scoped_creates_child_set(self):
        project = ProjectFactory()
        parent = ResultSetFactory(project=project, status=ResultSet.Status.READY)
        SetItemFactory(result_set=parent)
        lens_obj = save_lens(project, name="Child", lens=_lens(), result_set=parent)

        created = promote_lens(lens_obj, actor=None)

        lens_obj.refresh_from_db()
        assert created.parent_id == parent.id
        assert lens_obj.promoted_set_id == created.id

    def test_promote_project_scoped_creates_project_set(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.READY)
        SetItemFactory(result_set=result_set, disposition="r")
        lens_obj = save_lens(project, name="Proj", lens=_lens(dispositions=("r",)))

        created = promote_lens(lens_obj)

        created.refresh_from_db()
        assert created.project_id == project.id
        assert created.parent_id is None
        assert created.items.count() == 1


@pytest.mark.django_db
class TestActiveLens:
    def test_saved_lens_wins_over_querystring(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        lens_obj = save_lens(
            project,
            name="Saved",
            lens=_lens(dispositions=("i",)),
            result_set=result_set,
        )

        params = QueryDict(f"lens={lens_obj.id}&disposition=r")
        active, lens = active_lens(project, params, result_set=result_set)

        assert active == lens_obj
        assert lens.dispositions == ("i",)

    def test_unknown_lens_id_falls_back_to_querystring(self):
        project = ProjectFactory()
        params = QueryDict(f"lens={uuid.uuid4()}&disposition=r")
        active, lens = active_lens(project, params)
        assert active is None
        assert lens.dispositions == ("r",)

    def test_ad_hoc_without_lens(self):
        project = ProjectFactory()
        params = QueryDict("state=CA")
        active, lens = active_lens(project, params)
        assert active is None
        assert lens.states == ("CA",)


@pytest.mark.django_db
class TestLensViews:
    def _login(self, client):
        user = UserFactory()
        client.force_login(user)
        return user

    def test_save_view_creates_lens_and_redirects(self, client):
        user = self._login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)

        response = client.post(
            reverse("research:lens-save", args=[project.id]),
            {
                "scope": "set",
                "set": str(result_set.id),
                "name": "Mine",
                "lens": "disposition=r",
            },
        )

        assert response.status_code == 302
        assert LensModel.objects.filter(project=project, name="Mine").exists()

    def test_save_view_requires_login(self, client):
        project = ProjectFactory()
        response = client.post(
            reverse("research:lens-save", args=[project.id]),
            {"scope": "project", "name": "Mine"},
        )
        assert response.status_code == 302

    def test_delete_view(self, client):
        user = self._login(client)
        project = ProjectFactory(owner=user)
        lens_obj = LensFactory(project=project)
        response = client.post(
            reverse("research:lens-delete", args=[project.id, lens_obj.id])
        )
        assert response.status_code == 302
        assert not LensModel.objects.filter(pk=lens_obj.pk).exists()

    def test_promote_view(self, client):
        user = self._login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.READY)
        SetItemFactory(result_set=result_set)
        lens_obj = LensFactory(project=project, result_set=result_set, conditions={})
        response = client.post(
            reverse("research:lens-promote", args=[project.id, lens_obj.id]),
            {"name": "Promoted"},
        )
        assert response.status_code == 302
        lens_obj.refresh_from_db()
        assert lens_obj.promoted_set is not None
