import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from municipalities.models import Muni

from .models import SavedSearch, Search

User = get_user_model()


@pytest.mark.django_db
class TestSearchModel:
    def test_create_search_with_term(self):
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni, search_term="city council", all_results=False
        )
        assert search.muni == muni
        assert search.search_term == "city council"
        assert search.all_results is False
        assert search.created is not None
        assert search.modified is not None
        assert str(search.id)  # UUID is valid

    def test_create_search_without_term(self):
        muni = Muni.objects.create(
            subdomain="testcity2", name="Test City 2", state="NY", kind="city"
        )
        search = Search.objects.create(muni=muni, all_results=True)
        assert search.muni == muni
        assert search.search_term == ""
        assert search.all_results is True

    def test_str_representation_with_term(self):
        muni = Muni(name="San Francisco", state="CA")
        search = Search(muni=muni, search_term="budget")
        assert str(search) == "Search for 'budget' in San Francisco"

    def test_str_representation_without_term(self):
        muni = Muni(name="Los Angeles", state="CA")
        search = Search(muni=muni, all_results=True)
        assert str(search) == "Search in Los Angeles (all results: True)"

    def test_muni_required(self):
        with pytest.raises(IntegrityError):
            Search.objects.create(search_term="test search")

    def test_default_values(self):
        muni = Muni.objects.create(
            subdomain="defaults", name="Default Test", state="TX", kind="city"
        )
        search = Search.objects.create(muni=muni)
        assert search.search_term == ""
        assert search.all_results is False

    def test_related_name_searches(self):
        muni: Muni = Muni.objects.create(
            subdomain="related", name="Related Test", state="FL", kind="city"
        )
        search1 = Search.objects.create(muni=muni, search_term="first")
        search2 = Search.objects.create(muni=muni, search_term="second")

        assert search1 in muni.searches.all()  # type: ignore
        assert search2 in muni.searches.all()  # type: ignore
        assert muni.searches.count() == 2  # type: ignore

    def test_cascade_delete(self):
        muni = Muni.objects.create(
            subdomain="cascade", name="Cascade Test", state="WA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")
        search_id = search.id

        # Delete the muni, search should be deleted too
        muni.delete()
        assert not Search.objects.filter(id=search_id).exists()

    def test_meta_options(self):
        assert Search._meta.verbose_name == "Search"
        assert Search._meta.verbose_name_plural == "Searches"
        assert Search._meta.ordering == ["-created"]

    def test_search_update_search_with_all_results(self):
        """Test update_search method with all_results=True"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, all_results=True)

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "rows": [{"meeting": "Council", "date": "2024-01-01"}]
        }

        with patch.object(httpx, "get", return_value=mock_response):
            search.update_search()

        assert search.agenda_match_json == [
            {"meeting": "Council", "date": "2024-01-01"}
        ]
        assert search.last_agenda_matched is not None
        assert search.minutes_match_json == [
            {"meeting": "Council", "date": "2024-01-01"}
        ]
        assert search.last_minutes_matched is not None

    def test_search_update_search_with_search_term(self):
        """Test update_search method with search_term"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget review")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "rows": [{"meeting": "Budget", "date": "2024-02-01"}]
        }

        with patch.object(httpx, "get", return_value=mock_response):
            search.update_search()

        assert search.agenda_match_json == [{"meeting": "Budget", "date": "2024-02-01"}]
        assert search.last_agenda_matched is not None
        assert search.minutes_match_json == [
            {"meeting": "Budget", "date": "2024-02-01"}
        ]
        assert search.last_minutes_matched is not None

    def test_search_update_search_no_changes(self):
        """Test update_search when results haven't changed"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        existing_data = [{"meeting": "Council", "date": "2024-01-01"}]
        search = Search.objects.create(
            muni=muni,
            search_term="council",
            agenda_match_json=existing_data,
            minutes_match_json=existing_data,
        )

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rows": existing_data}

        with patch.object(httpx, "get", return_value=mock_response):
            search.update_search()

        # Should not update timestamps when data hasn't changed
        assert search.last_agenda_matched is None
        assert search.last_minutes_matched is None

    def test_search_update_search_empty_results(self):
        """Test update_search with empty results"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="nonexistent")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rows": []}

        with patch.object(httpx, "get", return_value=mock_response):
            search.update_search()

        # Should not update when results are empty
        assert search.agenda_match_json is None
        assert search.last_agenda_matched is None
        assert search.minutes_match_json is None
        assert search.last_minutes_matched is None

    def test_search_update_search_http_error(self):
        """Test update_search with HTTP error"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 500

        with patch.object(httpx, "get", return_value=mock_response):
            search.update_search()

        # Should not update on HTTP error
        assert search.agenda_match_json is None
        assert search.last_agenda_matched is None
        assert search.minutes_match_json is None
        assert search.last_minutes_matched is None


@pytest.mark.django_db
class TestSavedSearchModel:
    def test_savedsearch_str(self):
        """Test SavedSearch __str__ method"""
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="parks")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Parks Search"
        )

        assert str(saved_search) == "Parks Search - test@example.com"


@pytest.mark.django_db
class TestSavedSearchViews:
    def test_savedsearch_list_requires_auth(self, client):
        """Test that SavedSearch list view requires authentication"""
        from django.urls import reverse

        url = reverse("searches:savedsearch-list")
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_savedsearch_create_requires_auth(self, client):
        """Test that SavedSearch create view requires authentication"""
        from django.urls import reverse

        url = reverse("searches:savedsearch-create")
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_savedsearch_detail_requires_auth(self, client):
        """Test that SavedSearch detail view requires authentication"""
        import uuid

        from django.urls import reverse

        url = reverse("searches:savedsearch-detail", kwargs={"pk": uuid.uuid4()})
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_savedsearch_update_requires_auth(self, client):
        """Test that SavedSearch update view requires authentication"""
        import uuid

        from django.urls import reverse

        url = reverse("searches:savedsearch-update", kwargs={"pk": uuid.uuid4()})
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_savedsearch_delete_requires_auth(self, client):
        """Test that SavedSearch delete view requires authentication"""
        import uuid

        from django.urls import reverse

        url = reverse("searches:savedsearch-delete", kwargs={"pk": uuid.uuid4()})
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_savedsearch_list_shows_only_user_searches(self, client):
        """Test that SavedSearch list shows only the authenticated user's searches"""
        from django.urls import reverse

        # Create two users with saved searches
        user1 = User.objects.create_user(
            username="user1", password="pass1", email="user1@test.com"
        )  # type: ignore
        user2 = User.objects.create_user(
            username="user2", password="pass2", email="user2@test.com"
        )  # type: ignore

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="parks")

        _ = SavedSearch.objects.create(user=user1, search=search, name="User 1 Search")
        _ = SavedSearch.objects.create(user=user2, search=search, name="User 2 Search")

        # Login as user1
        client.force_login(user1)

        url = reverse("searches:savedsearch-list")
        response = client.get(url)

        assert response.status_code == 200
        assert "User 1 Search" in response.content.decode()
        assert "User 2 Search" not in response.content.decode()

    def test_savedsearch_form_valid_sets_user(self, client):
        """Test that form_valid automatically sets the current user"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", password="testpass", email="test@test.com"
        )  # type: ignore
        client.force_login(user)

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")

        url = reverse("searches:savedsearch-create")
        response = client.post(
            url, {"name": "My Budget Search", "search": str(search.id)}
        )

        # Should redirect after successful creation
        assert response.status_code == 302

        # Check that the saved search was created with the correct user
        saved_search = SavedSearch.objects.get(name="My Budget Search")
        assert saved_search.user == user
        assert saved_search.search == search

    def test_email_preview_requires_staff(self, client):
        """Test that email preview requires staff permissions"""
        from django.urls import reverse

        # Create test data
        user = User.objects.create_user(
            username="user", password="pass", email="user@test.com"
        )  # type: ignore
        staff = User.objects.create_user(
            username="staff", password="pass", email="staff@test.com", is_staff=True
        )  # type: ignore

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Test Search"
        )

        url = reverse(
            "searches:savedsearch-email-preview", kwargs={"pk": saved_search.id}
        )

        # Unauthenticated user should be redirected
        response = client.get(url)
        assert response.status_code == 302
        assert "/admin/login/" in response.url

        # Regular user should be redirected
        client.force_login(user)
        response = client.get(url)
        assert response.status_code == 302
        assert "/admin/login/" in response.url

        # Staff user should see the preview
        client.force_login(staff)
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=utf-8"
        assert b"Updates from" in response.content

    def test_email_preview_formats(self, client):
        """Test both HTML and text email preview formats"""
        from django.urls import reverse

        # Create test data
        staff = User.objects.create_user(
            username="staff", password="pass", email="staff@test.com", is_staff=True
        )  # type: ignore

        muni = Muni.objects.create(
            subdomain="oakland", name="Oakland", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni,
            search_term="budget",
            agenda_match_json=[
                {
                    "meeting": "City Council",
                    "date": "2025-06-01",
                    "text": "Budget discussion",
                }
            ],
        )
        saved_search = SavedSearch.objects.create(
            user=staff, search=search, name="Budget Updates"
        )

        client.force_login(staff)

        # Test HTML format (default)
        url_html = reverse(
            "searches:savedsearch-email-preview", kwargs={"pk": saved_search.id}
        )
        response = client.get(url_html)
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=utf-8"
        assert b"<h2>Updates from" in response.content
        assert b"City Council" in response.content

        # Test text format
        url_txt = reverse(
            "searches:savedsearch-email-preview-format",
            kwargs={"pk": saved_search.id, "format": "txt"},
        )
        response = client.get(url_txt)
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        content = response.content.decode()
        assert "We found new results for your saved search" in content
        assert "City Council" in content
        assert "<h2>" not in content  # No HTML in text version

    def test_savedsearch_send_notification(self, client):
        """Test the send_search_notification method"""
        from unittest.mock import patch

        user = User.objects.create_user(
            username="user", password="pass", email="user@test.com"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )

        # Mock the send method to avoid actually sending emails in tests
        with patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
            saved_search.send_search_notification()

            # Verify that send was called
            mock_send.assert_called_once()


@pytest.mark.django_db
class TestSavedSearchAdmin:
    def test_admin_preview_email_method(self):
        """Test the admin preview_email method"""

        from searches.admin import SavedSearchAdmin

        user = User.objects.create_user(
            username="user", password="pass", email="user@test.com"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Test Search"
        )

        admin = SavedSearchAdmin(SavedSearch, None)
        result = admin.preview_email(saved_search)

        # Should contain HTML links to both formats
        assert "href=" in result
        assert "HTML" in result
        assert "Text" in result
        assert str(saved_search.id) in result

    def test_admin_preview_email_links_method(self):
        """Test the admin preview_email_links method"""
        from searches.admin import SavedSearchAdmin

        user = User.objects.create_user(
            username="user", password="pass", email="user@test.com"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Test Search"
        )

        admin = SavedSearchAdmin(SavedSearch, None)
        result = admin.preview_email_links(saved_search)

        # Should contain detailed HTML links
        assert "Preview the email that would be sent" in result
        assert "View HTML Email" in result
        assert "View Plain Text Email" in result
        assert str(saved_search.id) in result

    def test_admin_preview_email_links_no_pk(self):
        """Test the admin preview_email_links method for unsaved objects"""
        from searches.admin import SavedSearchAdmin

        # Create unsaved object (no pk)
        saved_search = SavedSearch()

        admin = SavedSearchAdmin(SavedSearch, None)
        result = admin.preview_email_links(saved_search)

        # Should show message for unsaved objects
        assert "Save the search first" in result
