import pytest
from django.urls import reverse

from tests.factories import UserFactory


@pytest.mark.django_db
class TestNavigation:
    def test_base_navigation_has_research_and_no_notebooks(self, client):
        user = UserFactory()
        client.force_login(user)

        response = client.get(reverse("homepage"))

        content = response.content.decode()
        assert reverse("research:project-list") in content
        assert "Notebooks" not in content

    def test_homepage_links_to_research(self, client):
        response = client.get(reverse("homepage"))
        content = response.content.decode()
        assert reverse("research:project-list") in content
        assert "Notebooks" not in content
