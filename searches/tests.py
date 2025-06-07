import pytest
from django.contrib.admin.sites import AdminSite
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

    def test_search_update_search_sends_notifications_on_agenda_update(self):
        """Test update_search sends notifications when agenda is updated"""
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        SavedSearch.objects.create(user=user, search=search, name="Budget Search")

        # Mock the httpx calls and email sending
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "rows": [{"meeting": "Budget", "date": "2024-02-01"}]
        }

        with patch.object(httpx, "get", return_value=mock_response):
            with patch(
                "searches.models.SavedSearch.send_search_notification"
            ) as mock_send:
                search.update_search()

        # Should have called send_search_notification
        mock_send.assert_called_once()
        assert search.agenda_match_json == [{"meeting": "Budget", "date": "2024-02-01"}]
        assert search.last_agenda_matched is not None

    def test_search_update_search_sends_notifications_on_minutes_update(self):
        """Test update_search sends notifications when minutes are updated"""
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni,
            search_term="parks",
            agenda_match_json=[{"meeting": "Old", "date": "2023-01-01"}],
        )
        SavedSearch.objects.create(user=user, search=search, name="Parks Search")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200

        # Return old agenda data but new minutes data
        def side_effect(url):
            response = Mock()
            response.status_code = 200
            if "agendas" in url:
                response.json.return_value = {
                    "rows": [{"meeting": "Old", "date": "2023-01-01"}]
                }
            else:  # minutes
                response.json.return_value = {
                    "rows": [{"meeting": "Parks", "date": "2024-03-01"}]
                }
            return response

        with patch.object(httpx, "get", side_effect=side_effect):
            with patch(
                "searches.models.SavedSearch.send_search_notification"
            ) as mock_send:
                search.update_search()

        # Should have called send_search_notification for minutes update
        mock_send.assert_called_once()
        assert search.minutes_match_json == [{"meeting": "Parks", "date": "2024-03-01"}]
        assert search.last_minutes_matched is not None

    def test_search_update_search_no_notification_when_no_changes(self):
        """Test update_search does not send notifications when nothing changes"""
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass"
        )
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
        SavedSearch.objects.create(user=user, search=search, name="Council Search")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rows": existing_data}

        with patch.object(httpx, "get", return_value=mock_response):
            with patch(
                "searches.models.SavedSearch.send_search_notification"
            ) as mock_send:
                search.update_search()

        # Should NOT have called send_search_notification
        mock_send.assert_not_called()

    def test_search_update_search_notifies_multiple_saved_searches(self):
        """Test update_search notifies all saved searches for a search"""
        user1 = User.objects.create_user(  # type: ignore
            username="user1", email="user1@example.com", password="pass1"
        )
        user2 = User.objects.create_user(  # type: ignore
            username="user2", email="user2@example.com", password="pass2"
        )
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="zoning")
        SavedSearch.objects.create(user=user1, search=search, name="User1 Zoning")
        SavedSearch.objects.create(user=user2, search=search, name="User2 Zoning")

        # Mock the httpx calls
        from unittest.mock import Mock, patch

        import httpx

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "rows": [{"meeting": "Zoning", "date": "2024-04-01"}]
        }

        with patch.object(httpx, "get", return_value=mock_response):
            with patch(
                "searches.models.SavedSearch.send_search_notification"
            ) as mock_send:
                search.update_search()

        # Should have called send_search_notification twice (once for each saved search)
        assert mock_send.call_count == 2


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

    def test_savedsearch_last_notification_sent_field(self):
        """Test last_notification_sent field is None by default"""
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

        # Should be None by default
        assert saved_search.last_notification_sent is None


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
        user1 = User.objects.create_user(  # type: ignore
            username="user1", password="pass1", email="user1@test.com"
        )  # type: ignore
        user2 = User.objects.create_user(  # type: ignore
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

        user = User.objects.create_user(  # type: ignore
            username="testuser", password="testpass", email="test@test.com"
        )
        client.force_login(user)

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        url = reverse("searches:savedsearch-create")
        response = client.post(
            url,
            {
                "name": "My Budget Search",
                "municipality": str(muni.id),
                "search_term": "budget",
                "all_results": False,
            },
        )

        # Should redirect after successful creation
        assert response.status_code == 302

        # Check that the saved search was created with the correct user
        saved_search = SavedSearch.objects.get(name="My Budget Search")
        assert saved_search.user == user
        assert saved_search.search.search_term == "budget"

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

    def test_savedsearch_send_notification_updates_timestamp(self):
        """Test that send_search_notification updates last_notification_sent"""
        from unittest.mock import patch

        from django.utils import timezone

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

        # Verify it starts as None
        assert saved_search.last_notification_sent is None

        # Mock the send method and capture the time before sending
        with patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
            before_send = timezone.now()
            saved_search.send_search_notification()
            after_send = timezone.now()

            # Verify that send was called
            mock_send.assert_called_once()

            # Refresh from database to get updated value
            saved_search.refresh_from_db()

            # Verify timestamp was updated
            assert saved_search.last_notification_sent is not None
            assert before_send <= saved_search.last_notification_sent <= after_send


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

        admin = SavedSearchAdmin(SavedSearch, AdminSite())
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

        admin = SavedSearchAdmin(SavedSearch, AdminSite())
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

        admin = SavedSearchAdmin(SavedSearch, AdminSite())
        result = admin.preview_email_links(saved_search)

        # Should show message for unsaved objects
        assert "Save the search first" in result


@pytest.mark.django_db
class TestSearchManager:
    def test_get_or_create_for_params(self):
        """Test the SearchManager.get_or_create_for_params method"""
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        # First call should create
        search1, created1 = Search.objects.get_or_create_for_params(
            muni=muni, search_term="budget", all_results=False
        )
        assert created1 is True
        assert search1.search_term == "budget"
        assert search1.all_results is False

        # Second call with same params should return existing
        search2, created2 = Search.objects.get_or_create_for_params(
            muni=muni, search_term="budget", all_results=False
        )
        assert created2 is False
        assert search1.id == search2.id

        # Different params should create new
        search3, created3 = Search.objects.get_or_create_for_params(
            muni=muni, search_term="", all_results=True
        )
        assert created3 is True
        assert search3.id != search1.id


@pytest.mark.django_db
class TestSavedSearchCreateForm:
    def test_form_valid_with_search_term(self):
        """Test form validation with search term"""
        from searches.forms import SavedSearchCreateForm

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        form_data = {
            "name": "My Budget Search",
            "municipality": muni.id,
            "search_term": "budget",
            "all_results": False,
        }

        form = SavedSearchCreateForm(data=form_data)
        assert form.is_valid()

    def test_form_valid_with_all_results(self):
        """Test form validation with all_results"""
        from searches.forms import SavedSearchCreateForm

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        form_data = {
            "name": "All Updates",
            "municipality": muni.id,
            "search_term": "",
            "all_results": True,
        }

        form = SavedSearchCreateForm(data=form_data)
        assert form.is_valid()

    def test_form_invalid_neither_search_term_nor_all_results(self):
        """Test form validation fails when neither search_term nor all_results is provided"""
        from searches.forms import SavedSearchCreateForm

        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        form_data = {
            "name": "Invalid Search",
            "municipality": muni.id,
            "search_term": "",
            "all_results": False,
        }

        form = SavedSearchCreateForm(data=form_data)
        assert not form.is_valid()
        assert "You must either enter a search term" in str(form.errors)

    def test_form_save_creates_search_and_saved_search(self):
        """Test that form.save() creates both Search and SavedSearch objects"""
        from searches.forms import SavedSearchCreateForm

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        form_data = {
            "name": "My Budget Search",
            "municipality": muni.id,
            "search_term": "budget",
            "all_results": False,
        }

        form = SavedSearchCreateForm(data=form_data)
        assert form.is_valid()

        saved_search = form.save(user=user)

        # Check SavedSearch was created
        assert saved_search.name == "My Budget Search"
        assert saved_search.user == user

        # Check Search was created
        search = saved_search.search
        assert search.muni == muni
        assert search.search_term == "budget"
        assert search.all_results is False


@pytest.mark.django_db
class TestSavedSearchCreateView:
    def test_create_view_requires_auth(self, client):
        """Test that create view requires authentication"""
        from django.urls import reverse

        url = reverse("searches:savedsearch-create")
        response = client.get(url)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_create_view_get(self, client):
        """Test GET request to create view"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        client.force_login(user)

        url = reverse("searches:savedsearch-create")
        response = client.get(url)

        assert response.status_code == 200
        assert "Create Saved Search" in response.content.decode()

    def test_create_view_post_success(self, client):
        """Test successful POST to create view"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        client.force_login(user)

        url = reverse("searches:savedsearch-create")
        response = client.post(
            url,
            {
                "name": "My Budget Search",
                "municipality": muni.id,
                "search_term": "budget",
                "all_results": False,
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("searches:savedsearch-list")

        # Check objects were created
        saved_search = SavedSearch.objects.get(user=user)
        assert saved_search.name == "My Budget Search"
        assert saved_search.search.search_term == "budget"

    def test_create_view_prevents_duplicate(self, client):
        """Test that creating duplicate saved search shows error"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        client.force_login(user)

        # Create first saved search
        search, _ = Search.objects.get_or_create_for_params(
            muni=muni, search_term="budget", all_results=False
        )
        SavedSearch.objects.create(
            user=user, search=search, name="Existing Budget Search"
        )

        # Try to create duplicate
        url = reverse("searches:savedsearch-create")
        response = client.post(
            url,
            {
                "name": "Another Budget Search",
                "municipality": muni.id,
                "search_term": "budget",
                "all_results": False,
            },
        )

        assert response.status_code == 200  # Form redisplayed with errors
        assert "You already have a saved search" in response.content.decode()


@pytest.mark.django_db
class TestSavedSearchEditForm:
    def test_form_initialization_with_existing_search(self):
        """Test that form is properly initialized with existing search data"""
        from searches.forms import SavedSearchEditForm

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni, search_term="budget", all_results=False
        )
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )

        form = SavedSearchEditForm(instance=saved_search)

        assert form.fields["municipality"].initial == muni
        assert form.fields["search_term"].initial == "budget"
        assert form.fields["all_results"].initial is False

    def test_form_save_updates_search(self):
        """Test that form.save() updates the search object"""
        from searches.forms import SavedSearchEditForm

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni1 = Muni.objects.create(
            subdomain="city1", name="City 1", state="CA", kind="city"
        )
        muni2 = Muni.objects.create(
            subdomain="city2", name="City 2", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni1, search_term="budget", all_results=False
        )
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )

        form_data = {
            "name": "Planning Updates",
            "municipality": muni2.id,
            "search_term": "planning",
            "all_results": False,
        }

        form = SavedSearchEditForm(data=form_data, instance=saved_search)
        assert form.is_valid()

        updated_saved_search = form.save(user=user)

        assert updated_saved_search.name == "Planning Updates"
        assert updated_saved_search.search.muni == muni2
        assert updated_saved_search.search.search_term == "planning"
        assert updated_saved_search.search.all_results is False


@pytest.mark.django_db
class TestSavedSearchEditView:
    def test_edit_view_requires_auth(self, client):
        """Test that edit view requires authentication"""
        import uuid

        from django.urls import reverse

        url = reverse("searches:savedsearch-update", kwargs={"pk": uuid.uuid4()})
        response = client.get(url)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_edit_view_only_shows_user_searches(self, client):
        """Test that users can only edit their own searches"""
        from django.urls import reverse

        user1 = User.objects.create_user(
            username="user1", email="user1@example.com", password="testpass"
        )  # type: ignore
        user2 = User.objects.create_user(
            username="user2", email="user2@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search = SavedSearch.objects.create(
            user=user2, search=search, name="User 2 Search"
        )

        client.force_login(user1)

        url = reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        response = client.get(url)

        assert response.status_code == 404

    def test_edit_view_get(self, client):
        """Test GET request to edit view"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )
        client.force_login(user)

        url = reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        response = client.get(url)

        assert response.status_code == 200
        assert "Edit Saved Search" in response.content.decode()
        assert saved_search.name in response.content.decode()

    def test_edit_view_post_success(self, client):
        """Test successful POST to edit view"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="budget")
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Budget Updates"
        )
        client.force_login(user)

        url = reverse("searches:savedsearch-update", kwargs={"pk": saved_search.pk})
        response = client.post(
            url,
            {
                "name": "Planning Updates",
                "municipality": muni.id,
                "search_term": "planning",
                "all_results": False,
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("searches:savedsearch-list")

        # Check object was updated
        saved_search.refresh_from_db()
        assert saved_search.name == "Planning Updates"
        assert saved_search.search.search_term == "planning"

    def test_edit_view_prevents_duplicate(self, client):
        """Test that editing to duplicate saved search shows error"""
        from django.urls import reverse

        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )  # type: ignore
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        client.force_login(user)

        # Create two saved searches
        search1, _ = Search.objects.get_or_create_for_params(
            muni=muni, search_term="budget", all_results=False
        )
        search2, _ = Search.objects.get_or_create_for_params(
            muni=muni, search_term="planning", all_results=False
        )
        SavedSearch.objects.create(user=user, search=search1, name="Budget Updates")
        saved_search2 = SavedSearch.objects.create(
            user=user, search=search2, name="Planning Updates"
        )

        # Try to edit saved_search2 to have same parameters as saved_search1
        url = reverse("searches:savedsearch-update", kwargs={"pk": saved_search2.pk})
        response = client.post(
            url,
            {
                "name": "Another Budget Search",
                "municipality": muni.id,
                "search_term": "budget",
                "all_results": False,
            },
        )

        assert response.status_code == 200  # Form redisplayed with errors
        assert "You already have a saved search" in response.content.decode()


@pytest.mark.django_db
class TestMunicipalitySearch:
    def test_municipality_search_endpoint(self, client):
        """Test the municipality search HTMX endpoint"""
        from django.urls import reverse

        # Create test municipalities
        Muni.objects.create(
            subdomain="oakland", name="Oakland", state="CA", kind="city"
        )
        Muni.objects.create(
            subdomain="berkeley", name="Berkeley", state="CA", kind="city"
        )
        Muni.objects.create(
            subdomain="richmond", name="Richmond", state="VA", kind="city"
        )

        url = reverse("searches:municipality-search")

        # Test empty query returns all (limited)
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Berkeley" in content

        # Test search by name
        response = client.get(url, {"q": "Oakland"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Berkeley" not in content

        # Test search by state
        response = client.get(url, {"q": "VA"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Richmond" in content
        assert "Oakland" not in content

        # Test no results
        response = client.get(url, {"q": "nonexistent"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "No municipalities found" in content
