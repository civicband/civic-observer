# Umami Analytics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add comprehensive Umami analytics tracking to CivicObserver for page views, user actions, and HTMX events.

**Architecture:** New `analytics` Django app providing a context processor (injects tracking config into templates), template filters (`track_event`, `track_event_data`), and a JavaScript module for HTMX event tracking. User opt-out is controlled via admin-only field on User model.

**Tech Stack:** Django 5.2, Umami analytics (self-hosted), HTMX, Alpine.js

---

## Task 1: Create Analytics Django App

**Files:**
- Create: `analytics/__init__.py`
- Create: `analytics/apps.py`

**Step 1: Create the analytics app directory structure**

```bash
mkdir -p analytics/templatetags
touch analytics/__init__.py
touch analytics/templatetags/__init__.py
```

**Step 2: Create apps.py**

Create `analytics/apps.py`:

```python
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"
```

**Step 3: Commit**

```bash
git add analytics/
git commit -m "feat(analytics): create analytics app structure"
```

---

## Task 2: Add Umami Settings

**Files:**
- Modify: `config/settings/base.py`
- Modify: `config/settings/production.py`

**Step 1: Add settings to base.py**

Add at the end of `config/settings/base.py`:

```python
# Umami Analytics
UMAMI_ENABLED: bool = False
UMAMI_WEBSITE_ID: str = "522b42fb-2e46-4ba3-9803-4e17c7824958"
UMAMI_SCRIPT_URL: str = "https://analytics.civic.band/sunshine"
```

**Step 2: Add analytics app to LOCAL_APPS in base.py**

Modify `LOCAL_APPS` in `config/settings/base.py` to add `"analytics"`:

```python
LOCAL_APPS: list[str] = [
    "users",
    "municipalities",
    "searches",
    "meetings",
    "notebooks",
    "apikeys",
    "notifications",
    "analytics",
]
```

**Step 3: Enable Umami in production.py**

Add at the end of `config/settings/production.py`:

```python
# Umami Analytics - enabled in production
UMAMI_ENABLED: bool = True  # type: ignore[no-redef]
```

**Step 4: Commit**

```bash
git add config/settings/base.py config/settings/production.py
git commit -m "feat(analytics): add Umami settings configuration"
```

---

## Task 3: Add User Opt-Out Field

**Files:**
- Modify: `users/models.py`
- Modify: `users/admin.py`
- Create: `users/migrations/XXXX_add_analytics_opt_out.py` (auto-generated)

**Step 1: Add field to User model**

Modify `users/models.py` to add the opt-out field:

```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    analytics_opt_out = models.BooleanField(
        default=False,
        help_text="Exclude this user from analytics tracking (admin-only)",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
```

**Step 2: Create migration**

Run:
```bash
uv run python manage.py makemigrations users --name add_analytics_opt_out
```

Expected: Migration file created

**Step 3: Apply migration**

Run:
```bash
uv run python manage.py migrate
```

Expected: Migration applied successfully

**Step 4: Add field to UserAdmin**

Modify `users/admin.py` to expose the field in admin. Add `fieldsets` to `UserAdmin`:

```python
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "first_name", "last_name", "is_staff"]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
        "analytics_opt_out",
    ]
    search_fields = ["email", "username", "first_name", "last_name"]
    ordering = ["email"]

    # Add analytics_opt_out to the permissions fieldset
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Analytics", {"fields": ("analytics_opt_out",)}),
    )
```

**Step 5: Commit**

```bash
git add users/models.py users/admin.py users/migrations/
git commit -m "feat(users): add analytics_opt_out field for admin-controlled tracking exclusion"
```

---

## Task 4: Create Context Processor

**Files:**
- Create: `analytics/context_processors.py`
- Modify: `config/settings/base.py`
- Create: `tests/analytics/__init__.py`
- Create: `tests/analytics/test_context_processors.py`

**Step 1: Write the failing test**

Create `tests/analytics/__init__.py`:
```python
```

Create `tests/analytics/test_context_processors.py`:

```python
import pytest
from django.test import RequestFactory

from analytics.context_processors import umami_context


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.fixture
def anonymous_request(request_factory):
    from django.contrib.auth.models import AnonymousUser

    request = request_factory.get("/")
    request.user = AnonymousUser()
    return request


class TestUmamiContext:
    def test_returns_expected_keys(self, anonymous_request):
        """Context processor returns all required keys."""
        result = umami_context(anonymous_request)

        assert "umami_enabled" in result
        assert "umami_opted_out" in result
        assert "umami_website_id" in result
        assert "umami_script_url" in result

    def test_disabled_by_default(self, anonymous_request, settings):
        """Umami is disabled by default."""
        settings.UMAMI_ENABLED = False

        result = umami_context(anonymous_request)

        assert result["umami_enabled"] is False

    def test_enabled_when_setting_true(self, anonymous_request, settings):
        """Umami is enabled when UMAMI_ENABLED is True."""
        settings.UMAMI_ENABLED = True

        result = umami_context(anonymous_request)

        assert result["umami_enabled"] is True

    def test_respects_dnt_header(self, request_factory, settings):
        """Umami is disabled when DNT header is set."""
        from django.contrib.auth.models import AnonymousUser

        settings.UMAMI_ENABLED = True
        request = request_factory.get("/", HTTP_DNT="1")
        request.user = AnonymousUser()

        result = umami_context(request)

        assert result["umami_enabled"] is False

    def test_opted_out_for_anonymous_user(self, anonymous_request):
        """Anonymous users are not opted out."""
        result = umami_context(anonymous_request)

        assert result["umami_opted_out"] is False

    @pytest.mark.django_db
    def test_opted_out_when_user_has_flag(self, request_factory, user):
        """User with analytics_opt_out=True is opted out."""
        user.analytics_opt_out = True
        user.save()

        request = request_factory.get("/")
        request.user = user

        result = umami_context(request)

        assert result["umami_opted_out"] is True

    @pytest.mark.django_db
    def test_not_opted_out_when_user_flag_false(self, request_factory, user):
        """User with analytics_opt_out=False is not opted out."""
        user.analytics_opt_out = False
        user.save()

        request = request_factory.get("/")
        request.user = user

        result = umami_context(request)

        assert result["umami_opted_out"] is False

    def test_returns_website_id(self, anonymous_request, settings):
        """Context includes website ID from settings."""
        settings.UMAMI_WEBSITE_ID = "test-website-id"

        result = umami_context(anonymous_request)

        assert result["umami_website_id"] == "test-website-id"

    def test_returns_script_url(self, anonymous_request, settings):
        """Context includes script URL from settings."""
        settings.UMAMI_SCRIPT_URL = "https://example.com/script.js"

        result = umami_context(anonymous_request)

        assert result["umami_script_url"] == "https://example.com/script.js"
```

**Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/analytics/test_context_processors.py -v
```

Expected: FAIL with "No module named 'analytics.context_processors'"

**Step 3: Write the context processor**

Create `analytics/context_processors.py`:

```python
from django.conf import settings
from django.http import HttpRequest


def umami_context(request: HttpRequest) -> dict:
    """Inject Umami analytics configuration into template context."""
    enabled = getattr(settings, "UMAMI_ENABLED", False)
    opted_out = False

    if hasattr(request, "user") and request.user.is_authenticated:
        opted_out = getattr(request.user, "analytics_opt_out", False)

    # Respect Do Not Track header
    dnt = request.META.get("HTTP_DNT") == "1"

    return {
        "umami_enabled": enabled and not dnt,
        "umami_opted_out": opted_out,
        "umami_website_id": getattr(settings, "UMAMI_WEBSITE_ID", ""),
        "umami_script_url": getattr(settings, "UMAMI_SCRIPT_URL", ""),
    }
```

**Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/analytics/test_context_processors.py -v
```

Expected: PASS

**Step 5: Register context processor in settings**

Modify `config/settings/base.py` to add the context processor. Find the `TEMPLATES` setting and add to the `context_processors` list:

```python
TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "analytics.context_processors.umami_context",
            ],
        },
    },
]
```

**Step 6: Commit**

```bash
git add analytics/context_processors.py config/settings/base.py tests/analytics/
git commit -m "feat(analytics): add context processor for Umami configuration"
```

---

## Task 5: Create Template Filters

**Files:**
- Create: `analytics/templatetags/analytics.py`
- Create: `tests/analytics/test_templatetags.py`

**Step 1: Write the failing test**

Create `tests/analytics/test_templatetags.py`:

```python
import pytest
from django.template import Context, Template


class TestTrackEventFilter:
    def test_track_event_outputs_data_attribute(self):
        """track_event filter outputs data-umami-event attribute."""
        template = Template(
            '{% load analytics %}<button {{ "click_me"|track_event }}>Click</button>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="click_me"' in result

    def test_track_event_escapes_quotes(self):
        """track_event filter handles event names safely."""
        template = Template(
            '{% load analytics %}<button {{ "test_event"|track_event }}>Test</button>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="test_event"' in result


class TestTrackEventDataFilter:
    def test_track_event_data_outputs_both_attributes(self):
        """track_event_data filter outputs event and data attributes."""
        template = Template(
            '{% load analytics %}<a {{ "muni_viewed"|track_event_data:"alameda" }}>Link</a>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="muni_viewed"' in result
        assert 'data-umami-event-data="alameda"' in result

    def test_track_event_data_with_variable(self):
        """track_event_data filter works with template variables."""
        template = Template(
            '{% load analytics %}<a {{ "muni_viewed"|track_event_data:slug }}>Link</a>'
        )
        result = template.render(Context({"slug": "oakland"}))

        assert 'data-umami-event="muni_viewed"' in result
        assert 'data-umami-event-data="oakland"' in result
```

**Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/analytics/test_templatetags.py -v
```

Expected: FAIL with "TemplateSyntaxError: 'analytics' is not a registered tag library"

**Step 3: Write the template filters**

Create `analytics/templatetags/analytics.py`:

```python
"""Template filters for Umami analytics tracking."""

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def track_event(event_name: str) -> str:
    """
    Output Umami tracking attribute for an event.

    Usage: <button {{ "save_search"|track_event }}>Save</button>
    Output: <button data-umami-event="save_search">Save</button>
    """
    return mark_safe(f'data-umami-event="{escape(event_name)}"')


@register.filter
def track_event_data(event_name: str, data: str) -> str:
    """
    Output Umami tracking attribute with event data.

    Usage: <a {{ "muni_viewed"|track_event_data:municipality.slug }}>Link</a>
    Output: <a data-umami-event="muni_viewed" data-umami-event-data="oakland">Link</a>
    """
    return mark_safe(
        f'data-umami-event="{escape(event_name)}" data-umami-event-data="{escape(data)}"'
    )
```

**Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/analytics/test_templatetags.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add analytics/templatetags/analytics.py tests/analytics/test_templatetags.py
git commit -m "feat(analytics): add track_event and track_event_data template filters"
```

---

## Task 6: Create JavaScript Module for HTMX Events

**Files:**
- Create: `frontend/js/analytics.js`

**Step 1: Create the JavaScript directory and file**

```bash
mkdir -p frontend/js
```

Create `frontend/js/analytics.js`:

```javascript
/**
 * Umami Analytics - HTMX Event Tracking
 *
 * Tracks HTMX requests that have data-umami-htmx-event attributes.
 * The Umami script must be loaded before this runs.
 */
(function () {
  // Only run if Umami is loaded
  if (typeof umami === "undefined") return;

  // Track HTMX requests
  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var el = evt.detail.elt;
    var event = el.dataset.umamiHtmxEvent;
    if (event) {
      umami.track(event);
    }
  });
})();
```

**Step 2: Commit**

```bash
git add frontend/js/analytics.js
git commit -m "feat(analytics): add JavaScript module for HTMX event tracking"
```

---

## Task 7: Update Base Template

**Files:**
- Modify: `templates/base.html`

**Step 1: Add Umami script and analytics.js to base template**

Modify `templates/base.html` to add the Umami tracking script in the `<head>` section, after the existing scripts.

Find:
```html
        <!-- HTMX CSRF Configuration -->
        <script>
            document.addEventListener('DOMContentLoaded', function() {
```

Add before that block (after Alpine.js script):

```html
        <!-- Umami Analytics -->
        {% if umami_enabled and not umami_opted_out %}
        <script defer src="{{ umami_script_url }}"
                data-website-id="{{ umami_website_id }}"
                data-do-not-track="true"
                {% if user.is_authenticated %}data-tag="{{ user.id }}"{% endif %}></script>
        <script src="{% static 'js/analytics.js' %}" defer></script>
        {% endif %}
```

**Step 2: Verify the template loads without errors**

Run:
```bash
uv run pytest -v -k "test_" --maxfail=3
```

Expected: All tests pass

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(analytics): add Umami tracking script to base template"
```

---

## Task 8: Add Navigation Tracking

**Files:**
- Modify: `templates/base.html`

**Step 1: Load analytics template tags**

Add at the top of `templates/base.html`, after `{% load static tailwind_cli %}`:

```html
{% load analytics %}
```

**Step 2: Add tracking to navigation links**

Update the desktop navigation links in `templates/base.html`. Find the desktop navigation section and add tracking attributes:

```html
<!-- Desktop Navigation -->
<div class="hidden md:flex space-x-6">
    <a href="{% url 'homepage' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="home">Home</a>
    <a href="{% url 'munis:muni-list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="municipalities">Municipalities</a>
    <a href="{% url 'meetings:meeting-search' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="search">Search Meetings</a>
    <a href="{% url 'searches:savedsearch-list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="saved_searches">Saved Searches</a>
    {% if user.is_authenticated %}
        <a href="{% url 'notebooks:notebook-list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="notebooks">Notebooks</a>
        <a href="{% url 'apikeys:list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="api_keys">API Keys</a>
        <a href="{% url 'notifications:channel-list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="notifications">Notifications</a>
    {% endif %}
    {% if user.is_staff %} <a href="{% url 'admin:index' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="admin">Admin</a> {% endif %}
</div>
```

**Step 3: Add tracking to mobile navigation**

Update the mobile navigation links similarly:

```html
<div class="px-2 pt-2 pb-3 space-y-1 bg-white border-t border-gray-200">
    <a href="{% url 'homepage' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="home">Home</a>
    <a href="{% url 'munis:muni-list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="municipalities">Municipalities</a>
    <a href="{% url 'meetings:meeting-search' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="search">Search Meetings</a>
    <a href="{% url 'searches:savedsearch-list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="saved_searches">Saved Searches</a>
    {% if user.is_authenticated %}
        <a href="{% url 'notebooks:notebook-list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="notebooks">Notebooks</a>
        <a href="{% url 'apikeys:list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="api_keys">API Keys</a>
        <a href="{% url 'notifications:channel-list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="notifications">Notifications</a>
    {% endif %}
    {% if user.is_staff %}
        <a href="{% url 'admin:index' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="admin">Admin</a>
    {% endif %}
</div>
```

**Step 4: Add mobile menu open tracking**

Find the mobile menu button and add tracking:

```html
<button @click="mobileMenuOpen = !mobileMenuOpen"
        type="button"
        class="inline-flex items-center justify-center p-2 rounded-md text-gray-600 hover:text-gray-900 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-500 transition-colors"
        aria-controls="mobile-menu"
        :aria-expanded="mobileMenuOpen.toString()"
        data-umami-event="mobile_menu_open">
```

**Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat(analytics): add navigation event tracking"
```

---

## Task 9: Add Search Page Tracking

**Files:**
- Modify: `templates/meetings/meeting_search.html`

**Step 1: Read the current template**

First, examine the template to understand its structure.

**Step 2: Add HTMX search tracking**

Find the search input field and add `data-umami-htmx-event="search_query"`:

The search input should have `data-umami-htmx-event="search_query"` added to track search queries.

**Step 3: Add search result click tracking**

Search results should have click tracking added to result links.

**Step 4: Commit**

```bash
git add templates/meetings/meeting_search.html
git commit -m "feat(analytics): add search page event tracking"
```

---

## Task 10: Add Saved Search Tracking

**Files:**
- Modify: `templates/searches/savedsearch_form.html`
- Modify: `templates/searches/savedsearch_list.html`
- Modify: `templates/searches/savedsearch_detail.html`
- Modify: `templates/searches/savedsearch_confirm_delete.html`

**Step 1: Add tracking to saved search forms**

Add `{{ "saved_search_created"|track_event }}` or `{{ "saved_search_edited"|track_event }}` to submit buttons.

**Step 2: Add tracking to list view**

Track clicks on saved search items with `{{ "saved_search_viewed"|track_event }}`.

**Step 3: Add tracking to delete confirmation**

Track `{{ "saved_search_deleted"|track_event }}` on delete button.

**Step 4: Commit**

```bash
git add templates/searches/
git commit -m "feat(analytics): add saved search event tracking"
```

---

## Task 11: Add Notebook Tracking

**Files:**
- Modify: `templates/notebooks/notebook_form.html`
- Modify: `templates/notebooks/notebook_list.html`
- Modify: `templates/notebooks/notebook_confirm_delete.html`
- Modify: `templates/notebooks/partials/save_button.html`

**Step 1: Add tracking to notebook creation**

Add `{{ "notebook_created"|track_event }}` to create form submit.

**Step 2: Add tracking to notebook deletion**

Add `{{ "notebook_deleted"|track_event }}` to delete confirmation.

**Step 3: Add tracking to save-to-notebook action**

Add `{{ "page_saved_to_notebook"|track_event }}` to save button.

**Step 4: Commit**

```bash
git add templates/notebooks/
git commit -m "feat(analytics): add notebook event tracking"
```

---

## Task 12: Add Notification Channel Tracking

**Files:**
- Modify: `templates/notifications/channel_list.html`
- Modify: `templates/notifications/partials/channel_form.html`
- Modify: `templates/notifications/partials/channel_row.html`

**Step 1: Add tracking to channel creation**

Add `{{ "notification_channel_created"|track_event }}` to create form.

**Step 2: Add tracking to channel toggle/deletion**

Add appropriate tracking for channel enable/disable and delete actions.

**Step 3: Commit**

```bash
git add templates/notifications/
git commit -m "feat(analytics): add notification channel event tracking"
```

---

## Task 13: Add API Key Tracking

**Files:**
- Modify: `templates/apikeys/apikey_list.html`
- Modify: `templates/apikeys/partials/create_form.html`

**Step 1: Add tracking to API key creation**

Add `{{ "api_key_created"|track_event }}` to create form.

**Step 2: Add tracking to API key revocation**

Add `{{ "api_key_revoked"|track_event }}` to revoke buttons.

**Step 3: Commit**

```bash
git add templates/apikeys/
git commit -m "feat(analytics): add API key event tracking"
```

---

## Task 14: Add Municipality Tracking

**Files:**
- Modify: `templates/municipalities/muni_detail.html`
- Modify: `templates/municipalities/muni_list.html`

**Step 1: Add tracking to municipality detail view**

Track municipality page views with event data containing the municipality slug.

**Step 2: Add tracking to municipality list clicks**

Track clicks on municipality items from the list.

**Step 3: Commit**

```bash
git add templates/municipalities/
git commit -m "feat(analytics): add municipality event tracking"
```

---

## Task 15: Add Coverage Configuration and Run Full Tests

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add analytics to coverage configuration**

Update `pyproject.toml` to include analytics in coverage:

Find `addopts` in `[tool.pytest.ini_options]` and add `"--cov=analytics"`:

```toml
addopts = [
    "--reuse-db",
    "--cov=src",
    "--cov=users",
    "--cov=municipalities",
    "--cov=searches",
    "--cov=meetings",
    "--cov=notebooks",
    "--cov=apikeys",
    "--cov=notifications",
    "--cov=analytics",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-fail-under=70",
    "--strict-markers",
    "--disable-warnings",
]
```

**Step 2: Run full test suite**

Run:
```bash
uv run pytest
```

Expected: All tests pass with coverage >= 70%

**Step 3: Run type checking**

Run:
```bash
uv run mypy analytics/
```

Expected: No errors

**Step 4: Run linting**

Run:
```bash
uv run ruff check analytics/ tests/analytics/
```

Expected: No errors

**Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add analytics app to test coverage"
```

---

## Task 16: Final Review and Documentation

**Step 1: Run all pre-commit hooks**

Run:
```bash
uv run pre-commit run --all-files
```

Expected: All hooks pass

**Step 2: Verify all tests pass**

Run:
```bash
uv run pytest
```

Expected: All tests pass

**Step 3: Create final commit if any files were modified by hooks**

```bash
git add -A
git commit -m "chore: apply pre-commit formatting" --allow-empty
```

---

## Summary of Events Tracked

| Event | Location | Description |
|-------|----------|-------------|
| `nav_click` | base.html | Navigation link clicks (with destination property) |
| `mobile_menu_open` | base.html | Mobile menu opened |
| `search_query` | meeting_search.html | Search queries (HTMX) |
| `search_result_clicked` | meeting_search.html | Search result clicks |
| `saved_search_created` | savedsearch_form.html | New saved search created |
| `saved_search_edited` | savedsearch_form.html | Saved search modified |
| `saved_search_deleted` | savedsearch_confirm_delete.html | Saved search deleted |
| `saved_search_viewed` | savedsearch_list.html | Saved search clicked |
| `notebook_created` | notebook_form.html | New notebook created |
| `notebook_deleted` | notebook_confirm_delete.html | Notebook deleted |
| `page_saved_to_notebook` | save_button.html | Page saved to notebook |
| `notification_channel_created` | channel_form.html | Channel created |
| `notification_channel_deleted` | channel_row.html | Channel deleted |
| `notification_channel_toggled` | channel_row.html | Channel enabled/disabled |
| `api_key_created` | create_form.html | API key created |
| `api_key_revoked` | apikey_list.html | API key revoked |
| `muni_viewed` | muni_detail.html | Municipality page viewed |
