"""Tests for saved search views to ensure templates render correctly."""

import pytest
from django.urls import reverse

from tests.factories import MuniFactory, SavedSearchFactory, UserFactory


@pytest.mark.django_db
class TestSavedSearchListView:
    def test_list_view_renders(self, client):
        """Test saved search list page renders for authenticated user."""
        user = UserFactory()
        client.force_login(user)

        response = client.get(reverse("searches:savedsearch-list"))

        assert response.status_code == 200

    def test_list_view_with_saved_searches(self, client):
        """Test list view renders with saved searches."""
        user = UserFactory()
        muni = MuniFactory()
        saved_search = SavedSearchFactory(user=user)
        saved_search.search.municipalities.add(muni)

        client.force_login(user)

        response = client.get(reverse("searches:savedsearch-list"))

        assert response.status_code == 200
        assert saved_search.name in response.content.decode()


@pytest.mark.django_db
class TestSavedSearchDetailView:
    def test_detail_view_renders(self, client):
        """Test saved search detail page renders."""
        user = UserFactory()
        muni = MuniFactory()
        saved_search = SavedSearchFactory(user=user)
        saved_search.search.municipalities.add(muni)

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-detail", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200
        assert saved_search.name in response.content.decode()

    def test_detail_view_without_municipality(self, client):
        """Test detail view renders even without a municipality."""
        user = UserFactory()
        saved_search = SavedSearchFactory(user=user)
        # Don't add any municipalities

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-detail", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestSavedSearchEditView:
    def test_edit_view_renders(self, client):
        """Test saved search edit page renders."""
        user = UserFactory()
        muni = MuniFactory()
        saved_search = SavedSearchFactory(user=user)
        saved_search.search.municipalities.add(muni)

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200

    def test_edit_view_without_municipality(self, client):
        """Test edit view renders even without a municipality."""
        user = UserFactory()
        saved_search = SavedSearchFactory(user=user)
        # Don't add any municipalities

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200

    def test_edit_view_requires_authentication(self, client):
        """Test edit view redirects unauthenticated users."""
        saved_search = SavedSearchFactory()

        response = client.get(
            reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 302
        assert "/login/" in response.url

    def test_edit_view_only_shows_own_searches(self, client):
        """Test users can only edit their own saved searches."""
        user = UserFactory()
        other_user = UserFactory()
        saved_search = SavedSearchFactory(user=other_user)

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 404


@pytest.mark.django_db
class TestSavedSearchDeleteView:
    def test_delete_view_renders(self, client):
        """Test saved search delete confirmation page renders."""
        user = UserFactory()
        muni = MuniFactory()
        saved_search = SavedSearchFactory(user=user)
        saved_search.search.municipalities.add(muni)

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-delete", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200

    def test_delete_view_without_municipality(self, client):
        """Test delete view renders even without a municipality."""
        user = UserFactory()
        saved_search = SavedSearchFactory(user=user)
        # Don't add any municipalities

        client.force_login(user)

        response = client.get(
            reverse("searches:savedsearch-delete", kwargs={"pk": saved_search.pk})
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestSavedSearchCreateView:
    def test_create_view_renders(self, client):
        """Test saved search create page renders."""
        user = UserFactory()
        client.force_login(user)

        response = client.get(reverse("searches:savedsearch-create"))

        assert response.status_code == 200
