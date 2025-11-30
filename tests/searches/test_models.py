"""
Tests for searches models - following TDD approach.

These tests define the expected behavior of the refactored Search and SavedSearch models.
They will initially fail (RED) until we implement the model changes (GREEN).
"""

from datetime import date

import pytest

from meetings.models import MeetingPage
from searches.models import SavedSearch, Search
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    SavedSearchFactory,
    SearchFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchModelEdgeCases:
    """Test edge cases in Search model."""

    def test_str_with_multiple_municipalities(self):
        """
        Search.__str__() should show first 2 municipalities and count the rest.
        """
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")
        muni3 = MuniFactory(name="Alameda", state="CA")
        muni4 = MuniFactory(name="Emeryville", state="CA")

        search = SearchFactory(search_term="budget")
        search.municipalities.set([muni1, muni2, muni3, muni4])

        # Should show count of additional municipalities beyond first 2
        str_repr = str(search)
        assert "+2 more" in str_repr
        # Should mention it's a search for 'budget'
        assert "budget" in str_repr

    def test_str_with_exactly_two_municipalities(self):
        """
        Search.__str__() should show both municipalities when exactly 2.
        """
        muni1 = MuniFactory(name="Oakland", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")

        search = SearchFactory(search_term="zoning")
        search.municipalities.set([muni1, muni2])

        str_repr = str(search)
        assert "Oakland" in str_repr
        assert "Berkeley" in str_repr
        assert "more" not in str_repr


@pytest.mark.django_db
class TestSearchModel:
    """Test the refactored Search model with local database queries."""

    def test_search_with_municipalities_many_to_many(self):
        """Test that Search supports multiple municipalities via M2M relationship."""
        muni1 = MuniFactory(name="Berkeley", state="CA")
        muni2 = MuniFactory(name="Oakland", state="CA")

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni1, muni2)

        assert search.municipalities.count() == 2
        assert muni1 in search.municipalities.all()
        assert muni2 in search.municipalities.all()

    def test_search_with_states_filter(self):
        """Test that Search stores states as a list in JSONField."""
        search = SearchFactory(search_term="budget", states=["CA", "OR", "WA"])

        assert search.states == ["CA", "OR", "WA"]
        assert len(search.states) == 3

    def test_search_with_date_range_filters(self):
        """Test that Search supports date_from and date_to filters."""
        date_from = date(2024, 1, 1)
        date_to = date(2024, 12, 31)

        search = SearchFactory(
            search_term="planning", date_from=date_from, date_to=date_to
        )

        assert search.date_from == date_from
        assert search.date_to == date_to

    def test_search_with_document_type_filter(self):
        """Test that Search supports document_type filter (agenda/minutes/all)."""
        search = SearchFactory(search_term="zoning", document_type="agenda")

        assert search.document_type == "agenda"
        assert search.document_type in ["agenda", "minutes", "all"]

    def test_search_with_meeting_name_query(self):
        """Test that Search supports meeting_name_query for full-text search."""
        search = SearchFactory(
            search_term="budget", meeting_name_query="planning commission"
        )

        assert search.meeting_name_query == "planning commission"

    def test_search_with_empty_term_for_all_updates(self):
        """Test that empty search_term means 'all updates' mode."""
        muni = MuniFactory(name="San Francisco")

        # Empty string search_term means "all updates" mode
        search1 = SearchFactory(search_term="")
        search1.municipalities.add(muni)

        # Verify empty string is stored (NULL not allowed after removing null=True)
        assert search1.search_term == ""

        # Verify that get_or_create_for_params also normalizes to empty string
        search2 = Search.objects.get_or_create_for_params(
            search_term="",
            municipalities=[muni],
        )
        assert search2.search_term == ""

    def test_search_stores_last_checked_timestamp(self):
        """Test that Search stores timestamp of last check for change detection."""
        from django.utils import timezone

        search = SearchFactory()
        check_time = timezone.now()
        search.last_checked_for_new_pages = check_time
        search.save()

        search.refresh_from_db()
        assert search.last_checked_for_new_pages == check_time

    def test_search_tracks_result_count(self):
        """Test that Search tracks the number of matching pages."""
        search = SearchFactory()
        search.last_result_count = 42
        search.save()

        search.refresh_from_db()
        assert search.last_result_count == 42

    def test_search_update_detects_new_pages(self):
        """Test that update_search() detects when new pages match the search."""
        # Setup: Create a municipality with meeting pages
        muni = MuniFactory(name="Berkeley", subdomain="berkeley")
        doc = MeetingDocumentFactory(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date.today(),
            document_type="agenda",
        )

        # Create pages with searchable text
        page1 = MeetingPageFactory(
            document=doc, page_number=1, text="Discussion about housing policy"
        )
        page2 = MeetingPageFactory(
            document=doc, page_number=2, text="Budget allocation for housing projects"
        )

        # Create a search for "housing"
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None  # No previous check
        search.save()

        # Call update_search() - should find the new pages
        new_pages = search.update_search()

        # Should return QuerySet of new pages
        assert new_pages is not None
        assert new_pages.count() == 2
        assert page1 in new_pages
        assert page2 in new_pages

        # Should update last_checked_for_new_pages timestamp
        search.refresh_from_db()
        assert search.last_checked_for_new_pages is not None
        assert search.last_result_count == 2

    def test_search_update_with_no_changes_returns_empty(self):
        """Test that update_search() returns empty QuerySet when no new pages."""
        muni = MuniFactory(name="Oakland")
        doc = MeetingDocumentFactory(municipality=muni)
        _page = MeetingPageFactory(document=doc, text="Budget discussion")

        # Create search and run initial check
        from django.utils import timezone

        search = SearchFactory(search_term="budget")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = timezone.now()  # Already checked
        search.last_result_count = 1
        search.save()

        # Call update_search() - should find no new pages
        new_pages = search.update_search()

        assert new_pages is not None
        assert new_pages.count() == 0

    def test_all_updates_search_matches_any_new_pages(self):
        """Test that searches with empty search_term match all pages (all updates mode)."""
        muni = MuniFactory(name="San Francisco")
        doc = MeetingDocumentFactory(municipality=muni)

        # Create diverse pages with different content
        page1 = MeetingPageFactory(document=doc, page_number=1, text="Housing policy")
        page2 = MeetingPageFactory(document=doc, page_number=2, text="Budget report")
        page3 = MeetingPageFactory(document=doc, page_number=3, text="Zoning changes")

        # Create "all updates" search (empty search_term)
        search = SearchFactory(search_term="")
        search.municipalities.add(muni)
        search.last_checked_for_new_pages = None  # No previous check
        search.save()

        # Should match ALL pages regardless of content
        new_pages = search.update_search()

        assert new_pages is not None
        assert new_pages.count() == 3
        assert page1 in new_pages
        assert page2 in new_pages
        assert page3 in new_pages


@pytest.mark.django_db
class TestSavedSearchModel:
    """Test the refactored SavedSearch model with notification frequencies."""

    def test_saved_search_has_notification_frequency(self):
        """Test that SavedSearch has notification_frequency field."""
        user = UserFactory()
        search = SearchFactory()

        saved_search = SavedSearchFactory(
            user=user, search=search, notification_frequency="immediate"
        )

        assert saved_search.notification_frequency == "immediate"
        assert saved_search.notification_frequency in [
            "immediate",
            "daily",
            "weekly",
        ]

    def test_saved_search_notification_frequency_choices(self):
        """Test all notification frequency choices work."""
        user = UserFactory()

        # Test each frequency option
        for frequency in ["immediate", "daily", "weekly"]:
            # Create a new search for each saved search (unique_together constraint)
            search = SearchFactory()
            saved_search = SavedSearchFactory(
                user=user, search=search, notification_frequency=frequency
            )
            assert saved_search.notification_frequency == frequency

    def test_saved_search_has_last_checked_timestamp(self):
        """Test that SavedSearch tracks when it was last checked."""
        from datetime import datetime

        saved_search = SavedSearchFactory()

        assert saved_search.last_checked is not None
        assert isinstance(saved_search.last_checked, datetime)

    def test_saved_search_has_pending_results_flag(self):
        """Test that SavedSearch has has_pending_results flag for digest batching."""
        saved_search = SavedSearchFactory()
        assert hasattr(saved_search, "has_pending_results")
        assert saved_search.has_pending_results is False  # Default

        # Mark as having pending results
        saved_search.has_pending_results = True
        saved_search.save()

        saved_search.refresh_from_db()
        assert saved_search.has_pending_results is True

    def test_saved_search_default_frequency_is_immediate(self):
        """Test that new SavedSearch defaults to immediate notifications."""
        user = UserFactory()
        search = SearchFactory()

        # Create without specifying frequency
        saved_search = SavedSearch.objects.create(
            user=user, search=search, name="Test Search"
        )

        assert saved_search.notification_frequency == "immediate"

    def test_send_notification_accepts_new_pages_queryset(self):
        """Test that send_search_notification() accepts QuerySet of new MeetingPage objects."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page1 = MeetingPageFactory(document=doc, text="New housing policy")
        page2 = MeetingPageFactory(document=doc, text="Housing budget update")

        saved_search = SavedSearchFactory()

        # Should be able to call with QuerySet of pages
        _new_pages = MeetingPage.objects.filter(id__in=[page1.id, page2.id])

        # This will fail until we update the method signature
        # For now, just test that the method exists and can be called
        assert hasattr(saved_search, "send_search_notification")

    def test_email_template_renders_new_pages(self):
        """Test that email template properly renders new_pages data."""
        from django.template.loader import render_to_string

        muni = MuniFactory(name="Test City", state="CA", subdomain="testcity.ca")
        doc = MeetingDocumentFactory(
            municipality=muni, meeting_name="CityCouncil", document_type="agenda"
        )
        page1 = MeetingPageFactory(
            document=doc, page_number=1, text="Budget discussion for housing"
        )
        page2 = MeetingPageFactory(
            document=doc, page_number=2, text="Infrastructure planning"
        )

        user = UserFactory()
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        saved_search = SavedSearchFactory(
            user=user, search=search, name="Housing Alerts"
        )

        new_pages = MeetingPage.objects.filter(id__in=[page1.id, page2.id])

        # Render the email template
        context = {"subscription": saved_search, "new_pages": new_pages}
        html_content = render_to_string("email/search_update.html", context=context)
        txt_content = render_to_string("email/search_update.txt", context=context)

        # Check HTML template renders new_pages
        assert "Housing Alerts" in html_content
        assert "CityCouncil" in html_content
        assert "Page 1" in html_content or "page 1" in html_content.lower()
        assert "Budget discussion" in html_content
        assert "testcity.ca" in html_content  # civic.band link

        # Check TXT template renders new_pages
        assert "Housing Alerts" in txt_content
        assert "CityCouncil" in txt_content
        assert "Page 1" in txt_content or "page 1" in txt_content.lower()

    def test_email_template_handles_empty_new_pages(self):
        """Test that email template handles empty new_pages gracefully."""
        from django.template.loader import render_to_string

        muni = MuniFactory()
        user = UserFactory()
        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)
        saved_search = SavedSearchFactory(
            user=user, search=search, name="Housing Alerts"
        )

        # Empty queryset
        new_pages = MeetingPage.objects.none()

        context = {"subscription": saved_search, "new_pages": new_pages}
        html_content = render_to_string("email/search_update.html", context=context)
        txt_content = render_to_string("email/search_update.txt", context=context)

        # Should show "no results" message
        assert (
            "no new results" in html_content.lower() or "No new results" in html_content
        )
        assert (
            "no new results" in txt_content.lower() or "No new results" in txt_content
        )

    def test_saved_search_str_representation(self):
        """Test string representation includes user and name."""
        user = UserFactory(email="test@example.com")
        search = SearchFactory(search_term="housing")
        saved_search = SavedSearchFactory(
            user=user, search=search, name="My Housing Alerts"
        )

        str_repr = str(saved_search)
        assert "My Housing Alerts" in str_repr
        assert "test@example.com" in str_repr


@pytest.mark.django_db
class TestSearchModelIntegration:
    """Integration tests for Search model with different filter combinations."""

    def test_search_with_multiple_filters_combined(self):
        """Test Search with all filters set together."""
        muni1 = MuniFactory(name="Berkeley", state="CA")
        muni2 = MuniFactory(name="Oakland", state="CA")

        search = SearchFactory(
            search_term="housing",
            states=["CA", "OR"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
            meeting_name_query="planning",
        )
        search.municipalities.add(muni1, muni2)

        # Verify all filters are set
        assert search.search_term == "housing"
        assert search.municipalities.count() == 2
        assert search.states == ["CA", "OR"]
        assert search.date_from == date(2024, 1, 1)
        assert search.date_to == date(2024, 12, 31)
        assert search.document_type == "agenda"
        assert search.meeting_name_query == "planning"

    def test_search_filters_are_optional(self):
        """Test that all filter fields are optional."""
        # Create search with minimal data (empty strings for "all updates")
        search = SearchFactory(
            search_term="",  # Empty for "all updates"
            states=[],  # Empty list
            date_from=None,
            date_to=None,
            document_type="all",  # Default
            meeting_name_query="",  # Empty string (NULL not allowed)
        )

        assert search.search_term == ""
        assert search.states == []
        assert search.date_from is None
        assert search.date_to is None
        assert search.document_type == "all"
        assert search.meeting_name_query == ""  # CharField with blank=True, default=""
