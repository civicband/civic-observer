# tests/notebooks/test_tags.py
import pytest
from django.urls import reverse

from notebooks.models import Tag
from tests.factories import TagFactory, UserFactory


@pytest.mark.django_db
class TestTagListView:
    def test_returns_user_tags(self, client):
        """Test returns user's tags as HTML."""
        user = UserFactory()
        TagFactory(user=user, name="budget")
        TagFactory(user=user, name="housing")

        # Other user's tags shouldn't appear
        other_user = UserFactory()
        TagFactory(user=other_user, name="zoning")

        client.force_login(user)
        url = reverse("notebooks:tag-list")
        response = client.get(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        assert "budget" in content
        assert "housing" in content
        assert "zoning" not in content

    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        url = reverse("notebooks:tag-list")
        response = client.get(url)

        assert response.status_code == 302


@pytest.mark.django_db
class TestTagCreateView:
    def test_creates_tag(self, client):
        """Test creates new tag for user."""
        user = UserFactory()

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": "transportation"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Tag.objects.filter(user=user, name="transportation").exists()

    def test_returns_existing_if_duplicate(self, client):
        """Test returns existing tag if name already exists."""
        user = UserFactory()
        TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": "budget"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        # Should not create duplicate
        assert Tag.objects.filter(user=user, name="budget").count() == 1

    def test_normalizes_tag_name_to_lowercase(self, client):
        """Test tag names are normalized to lowercase."""
        user = UserFactory()

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": "BUDGET"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Tag.objects.filter(user=user, name="budget").exists()
        assert not Tag.objects.filter(user=user, name="BUDGET").exists()

    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        url = reverse("notebooks:tag-create")
        response = client.post(url, {"name": "test"})

        assert response.status_code == 302

    def test_empty_name_returns_400(self, client):
        """Test empty tag name returns 400 error."""
        user = UserFactory()

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": ""},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
