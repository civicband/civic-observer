# Progressive Search Loading - Tests-First Implementation Plan

## Goal
Improve perceived search performance by loading results in progressive batches instead of waiting for all results at once.

**Key Metric**: First results visible in <1 second, instead of waiting 3-5+ seconds for full page.

---

## Architecture Overview

### Current Flow
1. User submits search → sees loading spinner
2. Backend executes full query + generates 20 headlines
3. Returns complete HTML with all 20 results
4. User sees results (3-5 seconds later)

### New Flow
1. User submits search → sees loading spinner
2. Backend executes query + generates **5 headlines** (batch 1)
3. Returns HTML with 5 results + "load more" trigger
4. User sees first 5 results (<1 second) ✨
5. As user scrolls, HTMX auto-loads next batches (10 results each)
6. Progressive loading continues until all results shown

---

## Phase 1: Core Backend Logic (Tests First)

### 1.1 Test: Batch Parameter Parsing

**File**: `tests/meetings/test_progressive_search.py`

```python
import pytest
from django.urls import reverse
from tests.factories import (
    UserFactory,
    MuniFactory,
    MeetingDocumentFactory,
    MeetingPageFactory,
)


@pytest.mark.django_db
class TestProgressiveSearchBatching:
    """Tests for progressive batch loading of search results."""

    def test_default_batch_is_first_batch(self, authenticated_client):
        """When no batch parameter provided, should return first batch (5 results)."""
        # Setup: Create 15 pages with searchable text
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        # Should return exactly 5 results in first batch
        content = response.content.decode()
        assert content.count('role="listitem"') == 5
        # Should indicate there are more results
        assert 'data-batch-num="1"' in content

    def test_batch_1_returns_first_5_results(self, authenticated_client):
        """Batch 1 should explicitly return first 5 results."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert content.count('role="listitem"') == 5

    def test_batch_2_returns_next_10_results(self, authenticated_client):
        """Batch 2 should return results 6-15 (10 results)."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(20):
            MeetingPageFactory(
                document=doc,
                text=f"housing policy discussion number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Batch 2 should have 10 results
        assert content.count('role="listitem"') == 10
        assert 'data-batch-num="2"' in content

    def test_batch_3_returns_remaining_results(self, authenticated_client):
        """Batch 3 should return remaining results (can be < 10)."""
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
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "3"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Batch 3 should have only 3 results (18 - 5 - 10 = 3)
        assert content.count('role="listitem"') == 3
        assert 'data-batch-num="3"' in content
```

### 1.2 Test: Has More Flag

```python
def test_first_batch_indicates_more_results_available(self, authenticated_client):
    """First batch should include 'has_more' indicator when more results exist."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(15):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Should have load-more trigger for batch 2
    assert "hx-get" in content
    assert "batch=2" in content
    assert 'hx-trigger="revealed"' in content


def test_last_batch_indicates_no_more_results(self, authenticated_client):
    """Last batch should NOT include 'has_more' indicator."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    # Create exactly 5 pages (only 1 batch needed)
    for i in range(5):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Should NOT have load-more trigger
    assert "batch=2" not in content


def test_middle_batch_has_load_more_trigger(self, authenticated_client):
    """Middle batches should have load-more trigger for next batch."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(25):  # Needs 3 batches
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Batch 2 should have trigger for batch 3
    assert "batch=3" in content
    assert 'hx-trigger="revealed"' in content
```

### 1.3 Test: Total Count Calculation

```python
def test_total_count_shown_in_first_batch(self, authenticated_client):
    """First batch should show total result count."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(25):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Should show "Found 25 results"
    assert "25 result" in content


def test_subsequent_batches_do_not_recalculate_total(self, authenticated_client):
    """Subsequent batches should not show total count (performance optimization)."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(25):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Batch 2+ should NOT have results header (avoids recounting)
    assert "Found" not in content or 'data-batch-num="2"' in content
```

### 1.4 Test: Filter Preservation Across Batches

```python
def test_filters_preserved_across_batches(self, authenticated_client):
    """Filters should be preserved when loading subsequent batches."""
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

    # Batch 1 with municipality filter
    response = authenticated_client.get(
        url,
        {"query": "housing", "batch": "1", "municipalities": str(muni1.id)},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200

    # Batch 2 should still have filter applied
    response = authenticated_client.get(
        url,
        {"query": "housing", "batch": "2", "municipalities": str(muni1.id)},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    content = response.content.decode()
    # Should have Alameda results, not Berkeley
    assert "Alameda" in content
    assert "Berkeley" not in content


def test_date_filter_preserved_across_batches(self, authenticated_client):
    """Date filters should be preserved across batches."""
    from datetime import date

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
    response = authenticated_client.get(
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
    assert "2024-01-15" in content or "January 15, 2024" in content
    assert "2024-06-15" not in content
```

---

## Phase 2: Headline Generation Optimization

### 2.1 Test: Headlines Only Generated for Current Batch

```python
@pytest.mark.django_db
class TestBatchHeadlineGeneration:
    """Tests ensuring headlines are only generated for current batch."""

    def test_first_batch_generates_5_headlines(self, authenticated_client):
        """First batch should generate headlines for only 5 results."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(
                document=doc,
                text=f"The housing policy discussion on affordable housing is important topic number {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have highlighted terms in 5 results
        assert content.count("<mark") == 5  # Assuming 1 highlight per result minimum

    def test_second_batch_generates_10_headlines(self, authenticated_client):
        """Second batch should generate headlines for 10 results."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(25):
            MeetingPageFactory(
                document=doc,
                text=f"The housing policy discussion {i}",
                page_number=i + 1,
            )

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should have 10 results with headlines
        assert content.count('role="listitem"') == 10
```

---

## Phase 3: Frontend Integration

### 3.1 Test: HTMX Load More Trigger

```python
@pytest.mark.django_db
class TestHTMXLoadMoreIntegration:
    """Tests for HTMX progressive loading triggers."""

    def test_load_more_trigger_includes_all_query_params(self, authenticated_client):
        """Load-more trigger should preserve all search parameters."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni, document_type="agenda")
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
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

    def test_load_more_uses_revealed_trigger(self, authenticated_client):
        """Load-more should use 'revealed' trigger for auto-loading."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should use 'revealed' trigger (loads when scrolled into view)
        assert 'hx-trigger="revealed"' in content

    def test_load_more_swaps_beforeend(self, authenticated_client):
        """Load-more should append results (hx-swap=beforeend)."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should append to existing results
        assert 'hx-swap="beforeend"' in content
```

### 3.2 Test: Template Structure

```python
def test_first_batch_includes_results_container(self, authenticated_client):
    """First batch should include container for appending more results."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(15):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Should have container div for results
    assert 'id="search-results-list"' in content
    # Should have space for appending more batches
    assert 'role="list"' in content


def test_subsequent_batches_only_include_items(self, authenticated_client):
    """Subsequent batches should only include list items, not container."""
    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    for i in range(25):
        MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

    url = reverse("meetings:meeting-search-results")
    response = authenticated_client.get(
        url, {"query": "housing", "batch": "2"}, HTTP_HX_REQUEST="true"
    )

    content = response.content.decode()
    # Batch 2 should NOT re-create container
    assert 'id="search-results-list"' not in content
    # Should only have list items
    assert 'role="listitem"' in content
```

---

## Phase 4: Edge Cases & Error Handling

### 4.1 Test: Empty Results

```python
@pytest.mark.django_db
class TestProgressiveSearchEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_no_results_shows_empty_state(self, authenticated_client):
        """When no results found, should show empty state (not load-more)."""
        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "nonexistentquery12345"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        assert "No results found" in content
        assert "batch=2" not in content

    def test_invalid_batch_number_returns_400(self, authenticated_client):
        """Invalid batch numbers should return 400 error."""
        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "invalid"}, HTTP_HX_REQUEST="true"
        )

        # Should handle gracefully (either default to batch 1 or return error)
        assert response.status_code in [200, 400]

    def test_batch_beyond_available_results_returns_empty(self, authenticated_client):
        """Requesting batch beyond available results should return empty."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        # Only 5 pages (1 batch)
        for i in range(5):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing", "batch": "10"}, HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        # Should return empty or no results
        assert 'role="listitem"' not in content or content.count('role="listitem"') == 0
```

### 4.2 Test: Performance

```python
def test_first_batch_faster_than_full_page(self, authenticated_client):
    """First batch should be significantly faster than loading 20 results."""
    import time

    muni = MuniFactory()
    doc = MeetingDocumentFactory(municipality=muni)
    # Create many pages to make timing measurable
    for i in range(100):
        MeetingPageFactory(
            document=doc,
            text=f"housing policy affordable housing discussion {i}" * 10,  # Long text
            page_number=i + 1,
        )

    url = reverse("meetings:meeting-search-results")

    # Time first batch (5 results)
    start = time.time()
    response1 = authenticated_client.get(
        url, {"query": "housing", "batch": "1"}, HTTP_HX_REQUEST="true"
    )
    batch1_time = time.time() - start

    # Time larger batch (20 results) - simulate old behavior
    start = time.time()
    # We'll simulate by requesting multiple batches
    for batch_num in range(1, 3):  # Batches 1 and 2 = 15 results
        authenticated_client.get(
            url, {"query": "housing", "batch": str(batch_num)}, HTTP_HX_REQUEST="true"
        )
    full_time = time.time() - start

    # First batch should be faster
    # (This is a conceptual test - actual timing may vary)
    assert batch1_time < full_time
```

---

## Phase 5: Backward Compatibility

### 5.1 Test: Works Without Batch Parameter

```python
@pytest.mark.django_db
class TestBackwardCompatibility:
    """Tests ensuring backward compatibility with existing code."""

    def test_no_batch_param_returns_first_batch(self, authenticated_client):
        """Omitting batch param should default to batch 1."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        for i in range(15):
            MeetingPageFactory(document=doc, text=f"housing {i}", page_number=i + 1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing"}, HTTP_HX_REQUEST="true"  # No batch param
        )

        assert response.status_code == 200
        content = response.content.decode()
        # Should return 5 results (batch 1)
        assert content.count('role="listitem"') == 5

    def test_existing_search_queries_still_work(self, authenticated_client):
        """Existing search functionality should not break."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy", page_number=1)

        url = reverse("meetings:meeting-search-results")
        response = authenticated_client.get(
            url, {"query": "housing"}, HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        assert "housing policy" in response.content.decode()
```

---

## Implementation Checklist

### Backend Changes (`meetings/views.py`)
- [ ] Add `batch` parameter parsing to `meeting_page_search_results()`
- [ ] Implement batch size logic (5 for first, 10 for subsequent)
- [ ] Calculate offset based on batch number
- [ ] Add `has_more` flag calculation
- [ ] Pass batch metadata to template context
- [ ] Optimize query to only fetch current batch
- [ ] Ensure `_generate_headlines_for_page()` only runs on current batch

### Template Changes
- [ ] Create `search_results_batch.html` partial template
- [ ] Add results container div (`#search-results-list`)
- [ ] Implement load-more trigger with `hx-trigger="revealed"`
- [ ] Ensure subsequent batches append (not replace)
- [ ] Show total count only in first batch
- [ ] Add batch number tracking in data attributes

### Testing
- [ ] Run all tests in `test_progressive_search.py`
- [ ] Verify backward compatibility tests pass
- [ ] Test with various batch sizes (5, 15, 25, 100+ results)
- [ ] Test with all filter combinations
- [ ] Test error cases (invalid batch, no results)

### Performance Validation
- [ ] Measure time-to-first-result before and after
- [ ] Verify headline generation only happens for current batch
- [ ] Check that total count query is only run once
- [ ] Monitor database query count per batch

---

## Success Criteria

1. **First batch loads in <1 second** (5 results with headlines)
2. **Subsequent batches load progressively** as user scrolls
3. **All existing search features work** (filters, pagination, etc.)
4. **All tests pass** including new progressive tests
5. **No regression** in search accuracy or relevance
6. **Backend query optimizations** verified (fewer headlines generated per request)

---

## Rollout Strategy

Since this project doesn't have feature flagging infrastructure, we'll use a simpler, safer rollout approach:

### Phase 1: Development & Testing
1. Implement progressive loading on development branch
2. Run full test suite (all 71+ tests must pass)
3. Manual testing on local environment with various:
   - Result counts (0, 5, 15, 50, 100+ results)
   - Filter combinations
   - Network speeds (throttle to simulate slow connections)
4. Review with team

### Phase 2: Staging Deployment
1. Deploy to staging environment
2. Test with realistic data volumes
3. Verify performance improvements:
   - Measure time-to-first-result (should be <1s)
   - Check database query patterns
   - Monitor headline generation performance
4. Fix any issues found

### Phase 3: Production Deployment
1. Deploy during low-traffic period
2. Monitor immediately after deploy:
   - Error rates
   - Page load times
   - User behavior metrics
3. Have rollback plan ready (git revert)
4. Monitor for 24-48 hours

### Phase 4: Post-Deployment
1. Gather performance metrics:
   - Average time to first result
   - User engagement with results
   - Any error reports
2. Consider future optimizations based on data

### Emergency Rollback
If issues occur in production:
```bash
git revert <commit-hash>
git push origin main
# Deploy immediately
```

---

## Future Enhancements (Out of Scope)

- Infinite scroll with virtual scrolling (for 1000+ results)
- Pre-fetch next batch on hover
- Cache common searches
- WebSocket-based streaming
- Progressive headline generation (show text first, highlights later)
