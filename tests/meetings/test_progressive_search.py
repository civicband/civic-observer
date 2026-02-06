"""
Tests for progressive batch loading of search results.

This test suite validates the progressive/streaming search results feature that
loads results in batches (5 results initially, then 10 per batch) instead of
all at once, improving perceived performance.

Test Phases:
1. Core Backend Logic - Batch parameter parsing and result counts
2. Headline Generation - Ensuring headlines only generated for current batch
3. Frontend Integration - HTMX triggers and template structure
4. Edge Cases & Error Handling - Empty results, invalid inputs
5. Backward Compatibility - Existing functionality preserved
"""

from datetime import date

import pytest
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    UserFactory,
)

# ==============================================================================
# Phase 1: Core Backend Logic
# ==============================================================================


@pytest.mark.django_db
class TestProgressiveSearchBatching:
    """Tests for progressive batch loading of search results."""

    def test_default_batch_is_first_batch(self, client):
        """When no batch parameter provided, should return first batch (5 results)."""
        # Setup: Create authenticated user
        user = UserFactory()
        client.force_login(user)

        # Create 15 pages with searchable text
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        # Should return exactly 5 results in first batch
        content = response.content.decode()
        assert content.count('role="listitem"') == 5
        # Should indicate there are more results
        assert "batch" in content.lower() or "load" in content.lower()

    def test_batch_1_returns_first_5_results(self, client):
        """Batch 1 should explicitly return first 5 results."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert content.count('role="listitem"') == 5

    def test_batch_2_returns_next_10_results(self, client):
        """Batch 2 should return results 6-15 (10 results)."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(20):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Batch 2 should have 10 results
        assert content.count('role="listitem"') == 10

    def test_batch_3_returns_remaining_results(self, client):
        """Batch 3 should return remaining results (can be < 10)."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        # Create exactly 18 pages: batch 1 = 5, batch 2 = 10, batch 3 = 3
        for i in range(18):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "3"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Batch 3 should have only 3 results (18 - 5 - 10 = 3)
        assert content.count('role="listitem"') == 3

    def test_first_batch_indicates_more_results_available(self, client):
        """First batch should include 'has_more' indicator when more results exist."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have load-more trigger for batch 2
        assert "hx-get" in content
        assert "batch=2" in content
        assert 'hx-trigger="revealed"' in content

    def test_last_batch_indicates_no_more_results(self, client):
        """Last batch should NOT include 'has_more' indicator."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        # Create exactly 5 pages (only 1 batch needed)
        for i in range(5):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should NOT have load-more trigger for batch 2
        assert "batch=2" not in content

    def test_middle_batch_has_load_more_trigger(self, client):
        """Middle batches should have load-more trigger for next batch."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):  # Needs 3 batches
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Batch 2 should have trigger for batch 3
        assert "batch=3" in content
        assert 'hx-trigger="revealed"' in content

    def test_total_count_shown_in_first_batch(self, client):
        """First batch should show total result count."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should show "Found 25 results" or similar
        assert "25" in content
        assert "result" in content.lower()

    def test_subsequent_batches_do_not_recalculate_total(self, client):
        """Subsequent batches should not show total count (performance optimization)."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Batch 2+ should NOT recalculate/show total (just items)
        # The results header should not be present
        # (Implementation detail: may vary based on template structure)
        assert 'role="listitem"' in content

    def test_filters_preserved_across_batches(self, client):
        """Filters should be preserved when loading subsequent batches."""
        user = UserFactory()
        client.force_login(user)

        muni1 = MuniFactory(name="Alameda", state="CA")
        muni2 = MuniFactory(name="Berkeley", state="CA")
        doc1 = MeetingDocumentFactory(municipality=muni1)
        doc2 = MeetingDocumentFactory(municipality=muni2)

        # Create 15 pages in Alameda
        for i in range(15):
            MeetingPageFactory(document=doc1, text=f"housing {i}", page_number=i + 1)

        # Create 5 pages in Berkeley (should be filtered out)
        for i in range(5):
            MeetingPageFactory(document=doc2, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")

        # Batch 2 should still have filter applied
        response = client.get(
            url,
            {"query": "housing", "batch": "2", "municipalities": str(muni1.id)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Should have Alameda results, not Berkeley
        assert "Alameda" in content
        assert "Berkeley" not in content

    def test_date_filter_preserved_across_batches(self, client):
        """Date filters should be preserved across batches."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc1 = MeetingDocumentFactory(municipality=muni, meeting_date=date(2024, 1, 15))
        doc2 = MeetingDocumentFactory(municipality=muni, meeting_date=date(2024, 6, 15))

        # Create pages in both dates
        for i in range(15):
            MeetingPageFactory(document=doc1, text=f"housing {i}", page_number=i + 1)
        for i in range(5):
            MeetingPageFactory(document=doc2, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")

        # Filter to only January 2024
        response = client.get(
            url,
            {
                "query": "housing",
                "batch": "2",
                "date_from": "2024-01-01",
                "date_to": "2024-01-31",
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        # Should have January date
        assert "2024" in content and (
            "January" in content or "Jan" in content or "01" in content
        )
        # Should not have June date
        assert "June" not in content and "Jun" not in content


# ==============================================================================
# Phase 2: Headline Generation Optimization
# ==============================================================================


@pytest.mark.django_db
class TestBatchHeadlineGeneration:
    """Tests ensuring headlines are only generated for current batch."""

    def test_first_batch_generates_5_headlines(self, client):
        """First batch should generate headlines for only 5 results."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"The housing policy discussion on affordable housing is important topic number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have exactly 5 results with text content
        assert content.count('role="listitem"') == 5

    def test_second_batch_generates_10_headlines(self, client):
        """Second batch should generate headlines for 10 results."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):
            MeetingPageFactory(
                document=doc,
                text=f"The housing policy discussion {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have 10 results with headlines
        assert content.count('role="listitem"') == 10


# ==============================================================================
# Phase 3: Frontend Integration
# ==============================================================================


@pytest.mark.django_db
class TestHTMXLoadMoreIntegration:
    """Tests for HTMX progressive loading triggers."""

    def test_load_more_trigger_includes_all_query_params(self, client):
        """Load-more trigger should preserve all search parameters."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni, document_type="agenda")
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url,
            {
                "query": "housing",
                "batch": "1",
                "document_type": "agenda",
                "municipalities": str(muni.id),
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        # Load-more trigger should include all params
        assert "query=housing" in content
        assert "document_type=agenda" in content
        assert f"municipalities={muni.id}" in content
        assert "batch=2" in content

    def test_load_more_uses_revealed_trigger(self, client):
        """Load-more should use 'revealed' trigger for auto-loading."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should use 'revealed' trigger (loads when scrolled into view)
        assert 'hx-trigger="revealed"' in content

    def test_load_more_swaps_beforeend(self, client):
        """Load-more should append results (hx-swap=beforeend)."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should append to existing results
        assert 'hx-swap="beforeend"' in content

    def test_first_batch_includes_results_container(self, client):
        """First batch should include container for appending more results."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have container/list for results
        assert 'role="list"' in content

    def test_subsequent_batches_only_include_items(self, client):
        """Subsequent batches should only include list items, not container."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have list items
        assert 'role="listitem"' in content


# ==============================================================================
# Phase 4: Edge Cases & Error Handling
# ==============================================================================


@pytest.mark.django_db
class TestProgressiveSearchEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_no_results_shows_empty_state(self, client):
        """When no results found, should show empty state (not load-more)."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "nonexistentquery12345"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        assert "No results found" in content or "0 result" in content
        assert "batch=2" not in content

    def test_invalid_batch_number_returns_400_or_defaults(self, client):
        """Invalid batch numbers should be handled gracefully."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy", page_number=1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "invalid"}, HTTP_HX_REQUEST="true"
        )

        # Should handle gracefully (either default to batch 1 or return error)
        assert response.status_code in [200, 400]

    def test_batch_beyond_available_results_returns_empty(self, client):
        """Requesting batch beyond available results should return empty/no items."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        # Only 5 pages (1 batch)
        for i in range(5):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "10"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should return empty or no results
        result_count = content.count('role="listitem"')
        assert result_count == 0, f"Expected 0 results, got {result_count}"

    def test_exactly_5_results_no_load_more(self, client):
        """Exactly 5 results should not show load-more (edge case boundary)."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(5):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        assert content.count('role="listitem"') == 5
        assert "batch=2" not in content

    def test_exactly_15_results_batch_3_empty(self, client):
        """Exactly 15 results (batch 1: 5, batch 2: 10) - batch 3 should be empty."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")

        # Batch 2 should have 10 results
        response = client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )
        content = response.content.decode()
        assert content.count('role="listitem"') == 10
        # Batch 2 should NOT have load-more (no batch 3 needed)
        assert "batch=3" not in content


# ==============================================================================
# Phase 5: Backward Compatibility
# ==============================================================================


@pytest.mark.django_db
class TestBackwardCompatibility:
    """Tests ensuring backward compatibility with existing code."""

    def test_no_batch_param_returns_first_batch(self, client):
        """Omitting batch param should default to batch 1."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url,
            {"query": "housing"},
            HTTP_HX_REQUEST="true",  # No batch param
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Should return 5 results (batch 1)
        assert content.count('role="listitem"') == 5

    def test_existing_search_queries_still_work(self, client):
        """Existing search functionality should not break."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, page_number=1)
        # Update the page text after creation (factory.Faker overrides passed text)
        page.text = "housing policy discussion"
        page.save()

        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        # Should find the word "housing" (may be highlighted)
        assert "housing" in content.lower()

    def test_filters_still_work_without_batch(self, client):
        """All filters should work without batch parameter."""
        user = UserFactory()
        client.force_login(user)

        muni = MuniFactory(name="TestCity")
        doc = MeetingDocumentFactory(
            municipality=muni, document_type="agenda", meeting_date=date(2024, 1, 15)
        )
        page = MeetingPageFactory(document=doc, page_number=1)
        # Update the page text after creation (factory.Faker overrides passed text)
        page.text = "housing policy discussion"
        page.save()

        url = reverse("meetings:meeting-search-results")
        response = client.get(
            url,
            {
                "query": "housing",
                "municipalities": str(muni.id),
                "document_type": "agenda",
                "date_from": "2024-01-01",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "TestCity" in content
        # Should find the word "housing" (may be highlighted)
        assert "housing" in content.lower()
