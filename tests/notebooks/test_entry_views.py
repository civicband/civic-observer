# tests/notebooks/test_entry_views.py
import pytest
from django.urls import reverse

from notebooks.models import NotebookEntry, Tag
from tests.factories import (
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestEntryEditView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        notebook = NotebookFactory()
        entry = NotebookEntryFactory(notebook=notebook)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 302

    def test_can_edit_own_entry(self, client):
        """Test user can edit entry in their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook, note="")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "Updated note"})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert entry.note == "Updated note"

    def test_cannot_edit_other_users_entry(self, client):
        """Test user cannot edit entry in another user's notebook."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)
        entry = NotebookEntryFactory(notebook=notebook)

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 404

    def test_can_add_tags_to_entry(self, client):
        """Test user can add tags to entry."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        tag = TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "", "tags": [tag.pk]})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert tag in entry.tags.all()

    def test_can_create_new_tag_inline(self, client):
        """Test user can create a new tag while editing entry."""
        from notebooks.models import Tag

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "Test note", "new_tag": "NewTag"})

        entry.refresh_from_db()
        assert response.status_code == 302
        # Tag should be created (lowercase)
        assert Tag.objects.filter(user=user, name="newtag").exists()
        # Tag should be attached to entry
        assert entry.tags.filter(name="newtag").exists()

    def test_new_tag_is_normalized_to_lowercase(self, client):
        """Test new tags are normalized to lowercase."""
        from notebooks.models import Tag

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "", "new_tag": "UPPERCASE"})

        assert response.status_code == 302
        assert Tag.objects.filter(user=user, name="uppercase").exists()
        assert not Tag.objects.filter(user=user, name="UPPERCASE").exists()

    def test_existing_tag_is_reused(self, client):
        """Test that creating a tag with existing name reuses it."""
        from notebooks.models import Tag

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        existing_tag = TagFactory(user=user, name="existing")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "", "new_tag": "existing"})

        entry.refresh_from_db()
        assert response.status_code == 302
        # Should not create duplicate
        assert Tag.objects.filter(user=user, name="existing").count() == 1
        # Should attach the existing tag
        assert existing_tag in entry.tags.all()


@pytest.mark.django_db
class TestEntryDeleteView:
    def test_can_delete_own_entry(self, client):
        """Test user can delete entry from their notebook."""
        from notebooks.models import NotebookEntry

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        entry_pk = entry.pk

        client.force_login(user)
        url = reverse("notebooks:entry-delete", args=[notebook.pk, entry.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not NotebookEntry.objects.filter(pk=entry_pk).exists()

    def test_cannot_delete_other_users_entry(self, client):
        """Test user cannot delete entry from another user's notebook."""
        from notebooks.models import NotebookEntry

        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)
        entry = NotebookEntryFactory(notebook=notebook)
        entry_pk = entry.pk

        client.force_login(user)
        url = reverse("notebooks:entry-delete", args=[notebook.pk, entry.pk])
        response = client.post(url)

        assert response.status_code == 404
        assert NotebookEntry.objects.filter(pk=entry_pk).exists()


@pytest.mark.django_db
class TestSavePanelView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        page = MeetingPageFactory()
        url = reverse("notebooks:save-panel")
        response = client.get(url, {"page_id": str(page.id)})

        assert response.status_code == 302

    def test_returns_panel_html(self, client):
        """Test panel HTML is returned for authenticated users."""
        user = UserFactory()
        page = MeetingPageFactory()
        NotebookFactory(user=user)

        client.force_login(user)
        url = reverse("notebooks:save-panel")
        response = client.get(url, {"page_id": str(page.id)})

        assert response.status_code == 200
        assert b"Save to notebook" in response.content
        assert b"Save to Notebook" in response.content

    def test_shows_existing_tags(self, client):
        """Test user's existing tags are shown."""
        user = UserFactory()
        page = MeetingPageFactory()
        TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:save-panel")
        response = client.get(url, {"page_id": str(page.id)})

        assert response.status_code == 200
        assert b"budget" in response.content

    def test_shows_already_saved_message(self, client):
        """Test shows message when page already saved."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()
        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        client.force_login(user)
        url = reverse("notebooks:save-panel")
        response = client.get(url, {"page_id": str(page.id)})

        assert response.status_code == 200
        assert b"Already saved" in response.content

    def test_close_returns_empty_placeholder(self, client):
        """Test close action returns empty div."""
        user = UserFactory()
        page = MeetingPageFactory()

        client.force_login(user)
        url = reverse("notebooks:save-panel")
        response = client.get(url, {"page_id": str(page.id), "close": "1"})

        assert response.status_code == 200
        assert f'id="save-panel-{page.id}"'.encode() in response.content


@pytest.mark.django_db
class TestSavePageWithNotesAndTags:
    def test_save_with_note(self, client):
        """Test saving a page with a note."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
                "note": "Important budget discussion",
            },
        )

        assert response.status_code == 200
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=page)
        assert entry.note == "Important budget discussion"

    def test_save_with_existing_tags(self, client):
        """Test saving a page with existing tags."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()
        tag1 = TagFactory(user=user, name="budget")
        tag2 = TagFactory(user=user, name="zoning")

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
                "tags": [str(tag1.id), str(tag2.id)],
            },
        )

        assert response.status_code == 200
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=page)
        assert tag1 in entry.tags.all()
        assert tag2 in entry.tags.all()

    def test_save_with_new_tag(self, client):
        """Test saving a page with a new tag."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
                "new_tag": "NewCategory",
            },
        )

        assert response.status_code == 200
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=page)
        # Tag should be lowercase
        assert entry.tags.filter(name="newcategory").exists()
        assert Tag.objects.filter(user=user, name="newcategory").exists()

    def test_save_with_note_and_tags(self, client):
        """Test saving a page with both note and tags."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()
        tag = TagFactory(user=user, name="existing")

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
                "note": "Test note",
                "tags": [str(tag.id)],
                "new_tag": "newtag",
            },
        )

        assert response.status_code == 200
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=page)
        assert entry.note == "Test note"
        assert tag in entry.tags.all()
        assert entry.tags.filter(name="newtag").exists()

    def test_ignores_other_users_tags(self, client):
        """Test that tags from other users are ignored."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()
        other_tag = TagFactory(user=other_user, name="othertag")

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
                "tags": [str(other_tag.id)],
            },
        )

        assert response.status_code == 200
        entry = NotebookEntry.objects.get(notebook=notebook, meeting_page=page)
        assert other_tag not in entry.tags.all()

    def test_save_returns_oob_button_update(self, client):
        """Test that saving returns an out-of-band swap to update the button."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        page = MeetingPageFactory()

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {
                "page_id": str(page.id),
                "notebook_id": str(notebook.id),
            },
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Check for out-of-band swap attribute
        assert "hx-swap-oob" in content
        # Check button is updated to saved state
        assert f'id="save-btn-{page.id}"' in content
        assert "Saved" in content
