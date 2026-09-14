"""Tests for meeting search views: HTMX URL pushing and pagination behavior."""

from urllib.parse import urlencode

import pytest
from django.urls import reverse

from searches.models import PublicSearchPage
from tests.factories import MeetingPageFactory, SearchFactory

pytestmark = pytest.mark.django_db


def _results_url(params: dict) -> str:
    return f"{reverse('meetings:meeting-search-results')}?{urlencode(params)}"


class TestSearchResultsPushUrl:
    def test_htmx_success_sets_push_url(self, authenticated_client):
        MeetingPageFactory(text="housing policy discussion")
        response = authenticated_client.get(
            _results_url({"query": "housing"}), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        expected = f"{reverse('meetings:meeting-search')}?query=housing"
        assert response["HX-Push-Url"] == expected

    def test_push_url_includes_page_param(self, authenticated_client):
        MeetingPageFactory.create_batch(21, text="housing policy discussion")
        response = authenticated_client.get(
            _results_url({"query": "housing", "page": "2"}), HTTP_HX_REQUEST="true"
        )
        expected = f"{reverse('meetings:meeting-search')}?query=housing&page=2"
        assert response["HX-Push-Url"] == expected

    def test_no_push_url_on_error(self, authenticated_client):
        response = authenticated_client.get(_results_url({}), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "HX-Push-Url" not in response

    def test_no_push_when_suppressed(self, authenticated_client):
        MeetingPageFactory(text="housing policy discussion")
        response = authenticated_client.get(
            _results_url({"query": "housing"}),
            HTTP_HX_REQUEST="true",
            HTTP_X_NO_PUSH="true",
        )
        assert "HX-Push-Url" not in response

    def test_public_page_push_url_uses_public_path(self, client):
        search = SearchFactory(search_term="rent control")
        PublicSearchPage.objects.create(
            slug="rent-control", title="Rent Control", is_published=True, search=search
        )
        MeetingPageFactory(text="rent control ordinance")
        response = client.get(
            _results_url({"query": "rent control", "public_page_slug": "rent-control"}),
            HTTP_HX_REQUEST="true",
        )
        expected = "/topics/rent-control/?query=rent+control"
        assert response["HX-Push-Url"] == expected


class TestSearchResultsPaginationSwap:
    def test_pagination_links_use_window_top_swap(self, authenticated_client):
        MeetingPageFactory.create_batch(21, text="housing policy discussion")
        response = authenticated_client.get(
            _results_url({"query": "housing"}), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "show:window:top" in content
        assert "scroll:top" not in content


class TestSearchShellTemplate:
    def test_shell_form_uses_window_top_swap(self, authenticated_client):
        response = authenticated_client.get(reverse("meetings:meeting-search"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "show:window:top" in content
        assert 'hx-indicator="#search-loading"' not in content
