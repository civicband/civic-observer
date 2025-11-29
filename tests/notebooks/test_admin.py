import pytest
from django.urls import reverse

from tests.factories import (
    AdminUserFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
)


@pytest.mark.django_db
class TestNotebookAdmin:
    def test_notebook_list_accessible(self, client):
        """Test that notebook admin list is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)

        url = reverse("admin:notebooks_notebook_changelist")
        response = client.get(url)

        assert response.status_code == 200

    def test_notebook_detail_accessible(self, client):
        """Test that notebook admin detail is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)
        notebook = NotebookFactory()

        url = reverse("admin:notebooks_notebook_change", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 200


@pytest.mark.django_db
class TestTagAdmin:
    def test_tag_list_accessible(self, client):
        """Test that tag admin list is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)

        url = reverse("admin:notebooks_tag_changelist")
        response = client.get(url)

        assert response.status_code == 200

    def test_tag_detail_accessible(self, client):
        """Test that tag admin detail is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)
        tag = TagFactory()

        url = reverse("admin:notebooks_tag_change", args=[tag.pk])
        response = client.get(url)

        assert response.status_code == 200


@pytest.mark.django_db
class TestNotebookEntryAdmin:
    def test_entry_list_accessible(self, client):
        """Test that notebook entry admin list is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)

        url = reverse("admin:notebooks_notebookentry_changelist")
        response = client.get(url)

        assert response.status_code == 200

    def test_entry_detail_accessible(self, client):
        """Test that notebook entry admin detail is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)
        entry = NotebookEntryFactory()

        url = reverse("admin:notebooks_notebookentry_change", args=[entry.pk])
        response = client.get(url)

        assert response.status_code == 200
