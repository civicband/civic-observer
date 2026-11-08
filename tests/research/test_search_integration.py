import pytest
from django.urls import reverse

from research.models import SetItem
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    NotebookEntryFactory,
    NotebookFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchResultsAddToSet:
    def test_add_to_set_button_appears_for_authenticated_users(self, client):
        user = UserFactory()
        muni = MuniFactory()
        MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy discussion",
        )
        client.force_login(user)

        response = client.get(
            reverse("meetings:meeting-search-results"),
            {"query": "housing"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "Add to set" in response.content.decode()

    def test_notebook_entry_does_not_mark_a_result_in_a_set(self, client):
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        muni = MuniFactory()
        page = MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy discussion",
        )
        NotebookEntryFactory(notebook=notebook, meeting_page=page)
        client.force_login(user)

        response = client.get(
            reverse("meetings:meeting-search-results"),
            {"query": "housing"},
            HTTP_HX_REQUEST="true",
        )

        assert "In a set" not in response.content.decode()

    def test_saved_state_uses_research_set_items(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        muni = MuniFactory()
        page = MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy discussion",
        )
        SetItemFactory(result_set=result_set, page=page)
        client.force_login(user)

        response = client.get(
            reverse("meetings:meeting-search-results"),
            {"query": "housing"},
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert "In a set" in content
        assert SetItem.objects.filter(page=page).exists()
