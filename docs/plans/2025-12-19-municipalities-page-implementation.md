# Municipalities Page Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the municipalities list into a discovery tool with search, filters, pagination, and UTM-tracked links to civic.band.

**Architecture:** Extend the existing MuniCRUDView with django-filter FilterSet for filtering, add text search via query param, implement Django pagination, and update templates with new search/filter UI and UTM-tracked links.

**Tech Stack:** Django 5.2, django-filter, neapolitan CRUDView, HTMX, Alpine.js, TailwindCSS

---

## Task 1: Create Municipality FilterSet

**Files:**
- Create: `municipalities/filters.py`
- Test: `tests/municipalities/test_filters.py`

**Step 1: Write the failing test for state filter**

```python
# tests/municipalities/test_filters.py
import pytest
from django.utils import timezone

from municipalities.filters import MuniFilter
from municipalities.models import Muni


@pytest.fixture
def municipalities(db):
    """Create test municipalities."""
    now = timezone.now()
    return [
        Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
            last_updated=now,
        ),
        Muni.objects.create(
            subdomain="berkeley",
            name="Berkeley",
            state="CA",
            kind="City",
            pages=50,
            last_updated=now - timezone.timedelta(days=10),
        ),
        Muni.objects.create(
            subdomain="portland",
            name="Portland",
            state="OR",
            kind="City",
            pages=75,
            last_updated=now - timezone.timedelta(days=40),
        ),
    ]


class TestMuniFilter:
    def test_filter_by_state(self, municipalities):
        """Filter municipalities by state."""
        qs = Muni.objects.all()
        f = MuniFilter({"state": "CA"}, queryset=qs)
        assert f.qs.count() == 2
        assert all(m.state == "CA" for m in f.qs)
```

**Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter::test_filter_by_state -v`
Expected: FAIL with "cannot import name 'MuniFilter'"

**Step 3: Write minimal implementation**

```python
# municipalities/filters.py
import django_filters

from .models import Muni


class MuniFilter(django_filters.FilterSet):
    state = django_filters.CharFilter(field_name="state", lookup_expr="exact")

    class Meta:
        model = Muni
        fields = ["state"]
```

**Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter::test_filter_by_state -v`
Expected: PASS

**Step 5: Commit**

```bash
git add municipalities/filters.py tests/municipalities/
git commit -m "feat(municipalities): add state filter"
```

---

## Task 2: Add Kind and Activity Filters

**Files:**
- Modify: `municipalities/filters.py`
- Test: `tests/municipalities/test_filters.py`

**Step 1: Write failing tests for kind and activity filters**

```python
# Add to tests/municipalities/test_filters.py TestMuniFilter class


def test_filter_by_kind(self, municipalities):
    """Filter municipalities by type/kind."""
    # Add a county for testing
    Muni.objects.create(
        subdomain="alameda-county",
        name="Alameda County",
        state="CA",
        kind="County",
        pages=200,
    )
    qs = Muni.objects.all()
    f = MuniFilter({"kind": "City"}, queryset=qs)
    assert f.qs.count() == 3
    assert all(m.kind == "City" for m in f.qs)


def test_filter_by_activity_7_days(self, municipalities):
    """Filter municipalities updated in last 7 days."""
    qs = Muni.objects.all()
    f = MuniFilter({"activity": "7"}, queryset=qs)
    # Only Oakland was updated today
    assert f.qs.count() == 1
    assert f.qs.first().name == "Oakland"


def test_filter_by_activity_30_days(self, municipalities):
    """Filter municipalities updated in last 30 days."""
    qs = Muni.objects.all()
    f = MuniFilter({"activity": "30"}, queryset=qs)
    # Oakland (today) and Berkeley (10 days ago)
    assert f.qs.count() == 2


def test_filter_by_activity_90_days(self, municipalities):
    """Filter municipalities updated in last 90 days."""
    qs = Muni.objects.all()
    f = MuniFilter({"activity": "90"}, queryset=qs)
    # All three municipalities
    assert f.qs.count() == 3
```

**Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter -v`
Expected: FAIL for kind and activity tests

**Step 3: Implement kind and activity filters**

```python
# municipalities/filters.py
import django_filters
from django.utils import timezone

from .models import Muni


class ActivityFilter(django_filters.ChoiceFilter):
    """Filter by last_updated within N days."""

    def __init__(self, *args, **kwargs):
        kwargs["choices"] = [
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
            ("90", "Last 90 days"),
        ]
        kwargs["empty_label"] = "Any time"
        super().__init__(*args, **kwargs)

    def filter(self, qs, value):
        if not value:
            return qs
        days = int(value)
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return qs.filter(last_updated__gte=cutoff)


class MuniFilter(django_filters.FilterSet):
    state = django_filters.CharFilter(field_name="state", lookup_expr="exact")
    kind = django_filters.CharFilter(field_name="kind", lookup_expr="exact")
    activity = ActivityFilter(field_name="last_updated")

    class Meta:
        model = Muni
        fields = ["state", "kind", "activity"]
```

**Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add municipalities/filters.py tests/municipalities/test_filters.py
git commit -m "feat(municipalities): add kind and activity filters"
```

---

## Task 3: Add Text Search Filter

**Files:**
- Modify: `municipalities/filters.py`
- Test: `tests/municipalities/test_filters.py`

**Step 1: Write failing test for text search**

```python
# Add to tests/municipalities/test_filters.py TestMuniFilter class


def test_search_by_name(self, municipalities):
    """Search municipalities by name."""
    qs = Muni.objects.all()
    f = MuniFilter({"q": "oak"}, queryset=qs)
    assert f.qs.count() == 1
    assert f.qs.first().name == "Oakland"


def test_search_by_subdomain(self, municipalities):
    """Search municipalities by subdomain."""
    qs = Muni.objects.all()
    f = MuniFilter({"q": "port"}, queryset=qs)
    assert f.qs.count() == 1
    assert f.qs.first().subdomain == "portland"


def test_search_case_insensitive(self, municipalities):
    """Search is case insensitive."""
    qs = Muni.objects.all()
    f = MuniFilter({"q": "BERKELEY"}, queryset=qs)
    assert f.qs.count() == 1
    assert f.qs.first().name == "Berkeley"
```

**Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter::test_search_by_name -v`
Expected: FAIL

**Step 3: Add search filter**

```python
# municipalities/filters.py - update MuniFilter class
from django.db.models import Q


class MuniFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method="search_filter", label="Search")
    state = django_filters.CharFilter(field_name="state", lookup_expr="exact")
    kind = django_filters.CharFilter(field_name="kind", lookup_expr="exact")
    activity = ActivityFilter(field_name="last_updated")

    class Meta:
        model = Muni
        fields = ["q", "state", "kind", "activity"]

    def search_filter(self, queryset, name, value):
        """Search by name or subdomain (case insensitive)."""
        if not value:
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(subdomain__icontains=value))
```

**Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_filters.py::TestMuniFilter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add municipalities/filters.py tests/municipalities/test_filters.py
git commit -m "feat(municipalities): add text search filter"
```

---

## Task 4: Integrate FilterSet into View

**Files:**
- Modify: `municipalities/views.py`
- Test: `tests/municipalities/test_views.py`

**Step 1: Create test file and write failing test**

```python
# tests/municipalities/test_views.py
import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from municipalities.models import Muni


@pytest.fixture
def municipalities(db):
    """Create test municipalities."""
    now = timezone.now()
    return [
        Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
            last_updated=now,
        ),
        Muni.objects.create(
            subdomain="portland",
            name="Portland",
            state="OR",
            kind="City",
            pages=75,
            last_updated=now - timezone.timedelta(days=40),
        ),
    ]


class TestMuniListView:
    def test_list_all_municipalities(self, client: Client, municipalities):
        """List view shows all municipalities."""
        response = client.get(reverse("munis:muni-list"))
        assert response.status_code == 200
        assert "Oakland" in response.content.decode()
        assert "Portland" in response.content.decode()

    def test_filter_by_state(self, client: Client, municipalities):
        """Filter by state returns only matching municipalities."""
        response = client.get(reverse("munis:muni-list"), {"state": "CA"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Portland" not in content

    def test_search_by_name(self, client: Client, municipalities):
        """Search by name returns matching municipalities."""
        response = client.get(reverse("munis:muni-list"), {"q": "oak"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Oakland" in content
        assert "Portland" not in content
```

**Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListView::test_filter_by_state -v`
Expected: FAIL (filter not applied to view yet)

**Step 3: Integrate filter into view**

```python
# municipalities/views.py - update imports and MuniCRUDView
import json
import logging
import os

from django.db.models import Count
from django.http import JsonResponse
from django.views import View
from neapolitan.views import CRUDView

from .filters import MuniFilter
from .models import Muni

logger = logging.getLogger(__name__)


class MuniCRUDView(CRUDView):
    model = Muni
    url_base = "munis:muni"  # type: ignore
    fields = [
        "subdomain",
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "last_updated",
        "latitude",
        "longitude",
        "popup_data",
    ]
    list_display = [
        "name",
        "state",
        "kind",
        "pages",
        "last_updated",
    ]
    search_fields = ["name", "subdomain", "state"]
    filterset_fields = ["state", "kind", "country"]
    paginate_by = 25

    def get_queryset(self):
        queryset = super().get_queryset()

        # Apply filters
        self.filterset = MuniFilter(self.request.GET, queryset=queryset)
        queryset = self.filterset.qs

        # Handle sorting
        sort_by = self.request.GET.get("sort", "name")
        sort_order = self.request.GET.get("order", "asc")

        sort_fields = {
            "name": "name",
            "state": "state",
            "kind": "kind",
            "pages": "pages",
            "last_updated": "last_updated",
        }

        if sort_by in sort_fields:
            order_field = sort_fields[sort_by]
            if sort_order == "desc":
                order_field = f"-{order_field}"
            queryset = queryset.order_by(order_field)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filterset"] = getattr(self, "filterset", None)
        context["total_count"] = Muni.objects.count()
        return context

    # ... rest of the view stays the same
```

**Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListView -v`
Expected: PASS

**Step 5: Commit**

```bash
git add municipalities/views.py tests/municipalities/test_views.py
git commit -m "feat(municipalities): integrate filter into list view"
```

---

## Task 5: Add Pagination

**Files:**
- Modify: `municipalities/views.py`
- Test: `tests/municipalities/test_views.py`

**Step 1: Write failing test for pagination**

```python
# Add to tests/municipalities/test_views.py TestMuniListView class


def test_pagination_default_25_per_page(self, client: Client, db):
    """Pagination shows 25 municipalities per page."""
    # Create 30 municipalities
    for i in range(30):
        Muni.objects.create(
            subdomain=f"city-{i}",
            name=f"City {i}",
            state="CA",
            kind="City",
            pages=i,
        )
    response = client.get(reverse("munis:muni-list"))
    assert response.status_code == 200
    # Should have page_obj in context
    assert "page_obj" in response.context
    assert response.context["page_obj"].paginator.per_page == 25
    assert response.context["page_obj"].paginator.num_pages == 2


def test_pagination_page_2(self, client: Client, db):
    """Can navigate to page 2."""
    for i in range(30):
        Muni.objects.create(
            subdomain=f"city-{i}",
            name=f"City {i:02d}",  # Zero-pad for sorting
            state="CA",
            kind="City",
            pages=i,
        )
    response = client.get(reverse("munis:muni-list"), {"page": "2"})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 2
```

**Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListView::test_pagination_default_25_per_page -v`
Expected: FAIL (page_obj may not be present or pagination not working)

**Step 3: Ensure pagination is enabled in view**

The neapolitan CRUDView should handle pagination with `paginate_by = 25`. Verify the view has this set (we added it in Task 4). If tests fail, check that the template handles `page_obj`.

**Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListView::test_pagination_default_25_per_page -v`
Expected: PASS

**Step 5: Commit**

```bash
git add municipalities/views.py tests/municipalities/test_views.py
git commit -m "feat(municipalities): add pagination (25 per page)"
```

---

## Task 6: Update Template - Search Box and Filters

**Files:**
- Modify: `templates/municipalities/muni_list.html`
- Create: `templates/municipalities/partials/muni_filters.html`

**Step 1: Create filter partial template**

```html
<!-- templates/municipalities/partials/muni_filters.html -->
<div class="mb-6 space-y-4">
    <!-- Search Box -->
    <div class="max-w-xl mx-auto">
        <div class="relative">
            <input type="text"
                   name="q"
                   value="{{ request.GET.q|default:'' }}"
                   placeholder="Search by municipality name..."
                   class="w-full px-4 py-3 pl-10 text-gray-900 placeholder-gray-500 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                   hx-get="{% url 'munis:muni-list' %}"
                   hx-trigger="input changed delay:300ms"
                   hx-target="#muni-results"
                   hx-include="[name='state'], [name='kind'], [name='activity'], [name='sort'], [name='order']"
                   hx-push-url="true">
            <div class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                </svg>
            </div>
        </div>
    </div>

    <!-- Filter Dropdowns -->
    <div class="flex flex-wrap items-center justify-center gap-4">
        <!-- State Filter -->
        <select name="state"
                class="px-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                hx-get="{% url 'munis:muni-list' %}"
                hx-trigger="change"
                hx-target="#muni-results"
                hx-include="[name='q'], [name='kind'], [name='activity'], [name='sort'], [name='order']"
                hx-push-url="true">
            <option value="">All States</option>
            {% for state in state_choices %}
                <option value="{{ state.0 }}" {% if request.GET.state == state.0 %}selected{% endif %}>{{ state.1 }}</option>
            {% endfor %}
        </select>

        <!-- Type Filter -->
        <select name="kind"
                class="px-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                hx-get="{% url 'munis:muni-list' %}"
                hx-trigger="change"
                hx-target="#muni-results"
                hx-include="[name='q'], [name='state'], [name='activity'], [name='sort'], [name='order']"
                hx-push-url="true">
            <option value="">All Types</option>
            {% for kind in kind_choices %}
                <option value="{{ kind }}" {% if request.GET.kind == kind %}selected{% endif %}>{{ kind }}</option>
            {% endfor %}
        </select>

        <!-- Activity Filter -->
        <select name="activity"
                class="px-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                hx-get="{% url 'munis:muni-list' %}"
                hx-trigger="change"
                hx-target="#muni-results"
                hx-include="[name='q'], [name='state'], [name='kind'], [name='sort'], [name='order']"
                hx-push-url="true">
            <option value="">Any time</option>
            <option value="7" {% if request.GET.activity == "7" %}selected{% endif %}>Last 7 days</option>
            <option value="30" {% if request.GET.activity == "30" %}selected{% endif %}>Last 30 days</option>
            <option value="90" {% if request.GET.activity == "90" %}selected{% endif %}>Last 90 days</option>
        </select>

        <!-- Clear Filters -->
        {% if request.GET.q or request.GET.state or request.GET.kind or request.GET.activity %}
            <a href="{% url 'munis:muni-list' %}"
               class="text-sm text-indigo-600 hover:text-indigo-800 underline">
                Clear filters
            </a>
        {% endif %}
    </div>

    <!-- Results Count -->
    <div class="text-center text-sm text-gray-600">
        Showing {{ page_obj.paginator.count }} of {{ total_count }} municipalities
    </div>
</div>
```

**Step 2: Update view to provide filter choices**

```python
# municipalities/views.py - update get_context_data
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["filterset"] = getattr(self, "filterset", None)
    context["total_count"] = Muni.objects.count()

    # Get distinct values for filter dropdowns
    context["state_choices"] = Muni.STATE_FIELD_CHOICES
    context["kind_choices"] = (
        Muni.objects.values_list("kind", flat=True).distinct().order_by("kind")
    )
    return context
```

**Step 3: Update main list template to include filters**

Update `templates/municipalities/muni_list.html` to include the filter partial after the header.

**Step 4: Test manually in browser**

Run: `just dev` and visit `/municipalities/`
Expected: See search box and filter dropdowns

**Step 5: Commit**

```bash
git add templates/municipalities/
git commit -m "feat(municipalities): add search box and filter dropdowns"
```

---

## Task 7: Update Template - UTM Links and Actions

**Files:**
- Modify: `templates/municipalities/partials/muni_table_body.html`
- Modify: `templates/municipalities/muni_list.html`

**Step 1: Write test for UTM links**

```python
# Add to tests/municipalities/test_views.py
from django.utils import timezone


class TestMuniListViewLinks:
    def test_name_links_to_civic_band_with_utm(self, client: Client, db):
        """Municipality name links to civic.band with UTM parameters."""
        Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
            last_updated=timezone.now(),
        )
        response = client.get(reverse("munis:muni-list"))
        content = response.content.decode()
        assert "https://oakland.civic.band" in content
        assert "utm_source=civicobserver" in content
        assert "utm_medium=municipalities" in content
        assert "utm_campaign=municipality_list" in content
```

**Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListViewLinks -v`
Expected: FAIL (civic.band link not present)

**Step 3: Update table body template with UTM links**

```html
<!-- templates/municipalities/partials/muni_table_body.html -->
{% load utm %}
{% for muni in object_list %}
    <tr class="hover:bg-gray-50 transition-colors duration-150">
        <td class="px-6 py-4 whitespace-nowrap">
            <a href="{% civic_url 'https://'|add:muni.subdomain|add:'.civic.band' medium='municipalities' campaign='municipality_list' content='name_link' %}"
               class="text-indigo-600 hover:text-indigo-700 font-medium transition-colors duration-200"
               target="_blank"
               rel="noopener">
                {{ muni.name }}
            </a>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{{ muni.state }}</td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{{ muni.kind }}</td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                {{ muni.pages }} pages
            </span>
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
            {% if muni.last_updated %}
                {{ muni.last_updated|timesince }} ago
            {% else %}
                —
            {% endif %}
        </td>
        <td class="px-6 py-4 whitespace-nowrap text-sm font-medium">
            <div class="flex items-center space-x-2">
                <!-- Primary: Visit Site -->
                <a href="{% civic_url 'https://'|add:muni.subdomain|add:'.civic.band' medium='municipalities' campaign='municipality_list' content='visit_button' %}"
                   class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
                   target="_blank"
                   rel="noopener">
                    Visit Site
                </a>
                <!-- Secondary: Create Search -->
                <a href="{% url 'searches:savedsearch-create' %}?municipality={{ muni.pk }}"
                   class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 transition-colors">
                    Create Search
                </a>
                {% if user.is_staff %}
                    <!-- Staff menu -->
                    <div x-data="{ open: false }" class="relative">
                        <button @click="open = !open"
                                class="p-1.5 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100">
                            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z"/>
                            </svg>
                        </button>
                        <div x-show="open"
                             @click.outside="open = false"
                             class="absolute right-0 z-10 mt-1 w-32 bg-white rounded-md shadow-lg ring-1 ring-black ring-opacity-5">
                            <a href="{% url 'munis:muni-update' muni.pk %}"
                               class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100">
                                Edit
                            </a>
                            <a href="{% url 'munis:muni-delete' muni.pk %}"
                               class="block px-4 py-2 text-sm text-red-600 hover:bg-gray-100">
                                Delete
                            </a>
                        </div>
                    </div>
                {% endif %}
            </div>
        </td>
    </tr>
{% empty %}
    <tr>
        <td colspan="6" class="px-6 py-12 text-center text-gray-500">
            No municipalities match your search. Try adjusting your filters.
        </td>
    </tr>
{% endfor %}
```

**Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/municipalities/test_views.py::TestMuniListViewLinks -v`
Expected: PASS

**Step 5: Commit**

```bash
git add templates/municipalities/partials/muni_table_body.html
git commit -m "feat(municipalities): add UTM-tracked civic.band links and actions"
```

---

## Task 8: Handle Municipality Pre-fill in Search Creation

**Files:**
- Modify: `searches/views.py`
- Test: `tests/searches/test_views.py`

**Step 1: Write failing test for pre-fill**

```python
# Add to tests/searches/test_views.py or create new test file
import pytest
from django.urls import reverse

from municipalities.models import Muni


class TestSavedSearchCreatePreFill:
    def test_municipality_prefilled_from_query_param(self, authenticated_client, db):
        """Municipality field is pre-filled when query param provided."""
        muni = Muni.objects.create(
            subdomain="oakland",
            name="Oakland",
            state="CA",
            kind="City",
            pages=100,
        )
        response = authenticated_client.get(
            reverse("searches:savedsearch-create"),
            {"municipality": str(muni.pk)},
        )
        assert response.status_code == 200
        # Check that the form has municipality pre-selected
        form = response.context["form"]
        assert form.initial.get("municipality") == muni
```

**Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/searches/ -k "test_municipality_prefilled" -v`
Expected: FAIL

**Step 3: Update SavedSearchCreateView to handle pre-fill**

```python
# searches/views.py - update SavedSearchCreateView
class SavedSearchCreateView(CreateView):
    """Custom create view for saved searches using search parameters."""

    model = SavedSearch
    form_class = SavedSearchCreateForm
    template_name = "searches/savedsearch_create.html"
    success_url = reverse_lazy("searches:savedsearch-list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        """Pre-fill municipality from query parameter."""
        initial = super().get_initial()
        municipality_id = self.request.GET.get("municipality")
        if municipality_id:
            try:
                from municipalities.models import Muni

                initial["municipality"] = Muni.objects.get(pk=municipality_id)
            except (Muni.DoesNotExist, ValueError):
                pass
        return initial

    # ... rest stays the same
```

**Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest tests/searches/ -k "test_municipality_prefilled" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add searches/views.py tests/searches/
git commit -m "feat(searches): pre-fill municipality from query param"
```

---

## Task 9: Add Pagination UI to Template

**Files:**
- Create: `templates/municipalities/partials/pagination.html`
- Modify: `templates/municipalities/muni_list.html`

**Step 1: Create pagination partial**

```html
<!-- templates/municipalities/partials/pagination.html -->
{% if page_obj.has_other_pages %}
<nav class="flex items-center justify-center space-x-2 mt-8" aria-label="Pagination">
    {% if page_obj.has_previous %}
        <a href="?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ page_obj.previous_page_number }}"
           class="px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
           hx-get="{% url 'munis:muni-list' %}?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ page_obj.previous_page_number }}"
           hx-target="#muni-results"
           hx-push-url="true">
            <span class="sr-only">Previous</span>
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z" clip-rule="evenodd"/>
            </svg>
        </a>
    {% endif %}

    <!-- Page numbers -->
    {% for num in page_obj.paginator.page_range %}
        {% if page_obj.number == num %}
            <span class="px-4 py-2 text-sm font-medium text-white bg-indigo-600 border border-indigo-600 rounded-md">
                {{ num }}
            </span>
        {% elif num > page_obj.number|add:'-3' and num < page_obj.number|add:'3' %}
            <a href="?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ num }}"
               class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
               hx-get="{% url 'munis:muni-list' %}?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ num }}"
               hx-target="#muni-results"
               hx-push-url="true">
                {{ num }}
            </a>
        {% elif num == 1 or num == page_obj.paginator.num_pages %}
            <a href="?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ num }}"
               class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
               hx-get="{% url 'munis:muni-list' %}?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ num }}"
               hx-target="#muni-results"
               hx-push-url="true">
                {{ num }}
            </a>
        {% elif num == page_obj.number|add:'-3' or num == page_obj.number|add:'3' %}
            <span class="px-2 py-2 text-gray-500">...</span>
        {% endif %}
    {% endfor %}

    {% if page_obj.has_next %}
        <a href="?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ page_obj.next_page_number }}"
           class="px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
           hx-get="{% url 'munis:muni-list' %}?{% for key, value in request.GET.items %}{% if key != 'page' %}{{ key }}={{ value }}&{% endif %}{% endfor %}page={{ page_obj.next_page_number }}"
           hx-target="#muni-results"
           hx-push-url="true">
            <span class="sr-only">Next</span>
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"/>
            </svg>
        </a>
    {% endif %}
</nav>
{% endif %}
```

**Step 2: Include pagination in main template**

Add after the table in `muni_list.html`:
```html
{% include 'municipalities/partials/pagination.html' %}
```

**Step 3: Test manually**

Create enough municipalities to trigger pagination and verify in browser.

**Step 4: Commit**

```bash
git add templates/municipalities/
git commit -m "feat(municipalities): add pagination UI"
```

---

## Task 10: Update Mobile Card View

**Files:**
- Modify: `templates/municipalities/muni_list.html`

**Step 1: Update mobile card section**

Replace the mobile card section in `muni_list.html` with UTM links:

```html
<!-- Mobile: Card View -->
<div class="mt-8 md:hidden space-y-4">
    {% for muni in page_obj %}
        <div class="bg-white shadow-lg rounded-xl p-6 border border-gray-200">
            <div class="flex items-start justify-between mb-4">
                <div class="flex-1">
                    <a href="{% civic_url 'https://'|add:muni.subdomain|add:'.civic.band' medium='municipalities' campaign='municipality_list' content='name_link' %}"
                       class="text-lg font-semibold text-indigo-600 hover:text-indigo-700 transition-colors"
                       target="_blank"
                       rel="noopener">
                        {{ muni.name }}
                    </a>
                    <div class="mt-1 text-sm text-gray-600">
                        {{ muni.state }} &bull; {{ muni.kind }}
                    </div>
                </div>
            </div>

            <div class="flex flex-wrap gap-2 mb-4">
                <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-gray-100 text-gray-800">
                    {{ muni.pages }} pages
                </span>
                {% if muni.last_updated %}
                    <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-blue-100 text-blue-800">
                        {{ muni.last_updated|timesince }} ago
                    </span>
                {% endif %}
            </div>

            <div class="flex flex-col gap-2">
                <a href="{% civic_url 'https://'|add:muni.subdomain|add:'.civic.band' medium='municipalities' campaign='municipality_list' content='visit_button' %}"
                   class="inline-flex items-center justify-center px-4 py-2.5 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
                   target="_blank"
                   rel="noopener">
                    Visit Site
                </a>
                <a href="{% url 'searches:savedsearch-create' %}?municipality={{ muni.pk }}"
                   class="inline-flex items-center justify-center px-4 py-2.5 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 transition-colors">
                    Create Search
                </a>
                {% if user.is_staff %}
                    <div class="grid grid-cols-2 gap-2">
                        <a href="{% url 'munis:muni-update' muni.pk %}"
                           class="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200">
                            Edit
                        </a>
                        <a href="{% url 'munis:muni-delete' muni.pk %}"
                           class="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-red-600 bg-red-50 rounded-md hover:bg-red-100">
                            Delete
                        </a>
                    </div>
                {% endif %}
            </div>
        </div>
    {% empty %}
        <div class="text-center py-8 text-gray-500">
            No municipalities match your search.
        </div>
    {% endfor %}
</div>
```

**Step 2: Test on mobile viewport**

Resize browser to mobile width and verify cards display correctly.

**Step 3: Commit**

```bash
git add templates/municipalities/muni_list.html
git commit -m "feat(municipalities): update mobile card view with UTM links"
```

---

## Task 11: Update Page Header and Subtitle

**Files:**
- Modify: `templates/municipalities/muni_list.html`

**Step 1: Update header text**

Change the subtitle from "Manage and monitor" to "Explore":

```html
<div>
    <h1 class="text-3xl font-bold tracking-tight text-gray-900">Municipalities</h1>
    <p class="mt-2 text-sm text-gray-600">Explore municipal sites across the CivicBand network</p>
</div>
```

**Step 2: Commit**

```bash
git add templates/municipalities/muni_list.html
git commit -m "chore(municipalities): update page subtitle for discovery focus"
```

---

## Task 12: Run Full Test Suite and Final Cleanup

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run pytest --tb=short -q`
Expected: All tests pass

**Step 2: Run linting and type checking**

Run: `uv run --group dev ruff check . && uv run --group dev mypy municipalities/`
Expected: No errors

**Step 3: Manual testing checklist**

- [ ] Search box filters results as you type
- [ ] State dropdown filters correctly
- [ ] Type dropdown filters correctly
- [ ] Activity dropdown filters correctly
- [ ] "Clear filters" link appears when filters active
- [ ] Pagination works with filters preserved
- [ ] Name links go to civic.band with UTM params
- [ ] "Visit Site" button works
- [ ] "Create Search" pre-fills municipality
- [ ] Staff users see edit/delete menu
- [ ] Mobile view works correctly

**Step 4: Final commit**

```bash
git add .
git commit -m "feat(municipalities): complete page redesign for discovery"
```

---

## Summary

This plan implements:
1. **FilterSet** with state, kind, activity, and text search filters
2. **Pagination** at 25 per page with HTMX navigation
3. **UTM-tracked links** to civic.band for analytics
4. **Updated actions**: "Visit Site" (primary) and "Create Search" (secondary)
5. **Staff admin menu** for edit/delete actions
6. **Municipality pre-fill** on search creation page
7. **Updated mobile card view** matching desktop functionality

All tasks follow TDD with bite-sized commits.
