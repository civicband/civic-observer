# UTM Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add UTM tracking parameters to civic ecosystem outbound links for attribution analytics.

**Architecture:** Django template tag `{% civic_url %}` that adds UTM parameters (utm_source, utm_medium, utm_campaign, utm_content) to URLs matching civic ecosystem domains. Server-side only, no JavaScript.

**Tech Stack:** Django template tags, urllib.parse for URL manipulation, pytest for testing.

---

## Task 1: Create UTM Template Tag with Tests

**Files:**
- Create: `analytics/templatetags/utm.py`
- Create: `tests/analytics/test_utm.py`

### Step 1: Write the failing test for basic civic.band URL

Create `tests/analytics/test_utm.py`:

```python
import pytest
from django.template import Context, Template


class TestCivicUrlTag:
    def test_adds_utm_params_to_civic_band_url(self):
        """civic_url tag adds UTM parameters to civic.band URLs."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://alameda.ca.civic.band/meetings/agendas/123" '
            'medium="search" campaign="search_results" content="view_button" %}'
        )
        result = template.render(Context({}))

        assert "utm_source=civicobserver" in result
        assert "utm_medium=search" in result
        assert "utm_campaign=search_results" in result
        assert "utm_content=view_button" in result
        assert result.startswith("https://alameda.ca.civic.band/meetings/agendas/123?")
```

### Step 2: Run test to verify it fails

Run: `uv run pytest tests/analytics/test_utm.py -v`
Expected: FAIL with "No module named 'analytics.templatetags.utm'" or similar

### Step 3: Write minimal implementation

Create `analytics/templatetags/utm.py`:

```python
"""Template tag for adding UTM parameters to civic ecosystem URLs."""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django import template

register = template.Library()

# Domains that should receive UTM parameters
CIVIC_DOMAINS = [
    "civic.band",
    "docs.civic.band",
]


def is_civic_domain(hostname: str) -> bool:
    """Check if hostname belongs to civic ecosystem."""
    if not hostname:
        return False
    for domain in CIVIC_DOMAINS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True
    return False


@register.simple_tag
def civic_url(
    url: str,
    medium: str,
    campaign: str,
    content: str = "link",
) -> str:
    """
    Add UTM parameters to a civic ecosystem URL.

    Usage:
        {% load utm %}
        {% civic_url page_url medium='search' campaign='search_results' content='view_button' %}

    Returns the URL unchanged if it's not a civic ecosystem domain.
    """
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return url

    # Only add UTM params to civic ecosystem domains
    if not is_civic_domain(parsed.netloc):
        return url

    # Parse existing query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    # Add/overwrite UTM parameters
    utm_params = {
        "utm_source": ["civicobserver"],
        "utm_medium": [medium],
        "utm_campaign": [campaign],
        "utm_content": [content],
    }
    query_params.update(utm_params)

    # Rebuild the URL
    new_query = urlencode(query_params, doseq=True)
    new_parsed = parsed._replace(query=new_query)

    return urlunparse(new_parsed)
```

### Step 4: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_adds_utm_params_to_civic_band_url -v`
Expected: PASS

### Step 5: Add test for docs.civic.band

Add to `tests/analytics/test_utm.py`:

```python
def test_adds_utm_params_to_docs_civic_band(self):
    """civic_url tag adds UTM parameters to docs.civic.band URLs."""
    template = Template(
        "{% load utm %}"
        '{% civic_url "https://docs.civic.band/api/reference" '
        'medium="nav" campaign="footer" content="docs_link" %}'
    )
    result = template.render(Context({}))

    assert "utm_source=civicobserver" in result
    assert "utm_medium=nav" in result
    assert "utm_campaign=footer" in result
    assert "utm_content=docs_link" in result
```

### Step 6: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_adds_utm_params_to_docs_civic_band -v`
Expected: PASS (implementation already handles this)

### Step 7: Add test for non-civic URLs unchanged

Add to `tests/analytics/test_utm.py`:

```python
def test_non_civic_url_unchanged(self):
    """civic_url tag returns non-civic URLs unchanged."""
    template = Template(
        "{% load utm %}"
        '{% civic_url "https://google.com/search" '
        'medium="search" campaign="search_results" %}'
    )
    result = template.render(Context({}))

    assert result == "https://google.com/search"
    assert "utm_" not in result
```

### Step 8: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_non_civic_url_unchanged -v`
Expected: PASS

### Step 9: Add test for fake civic domain (security)

Add to `tests/analytics/test_utm.py`:

```python
def test_fake_civic_domain_unchanged(self):
    """civic_url tag doesn't add UTM to domains that look like civic.band but aren't."""
    template = Template(
        "{% load utm %}"
        '{% civic_url "https://civic.band.fake.com/page" '
        'medium="search" campaign="test" %}'
    )
    result = template.render(Context({}))

    assert result == "https://civic.band.fake.com/page"
    assert "utm_" not in result
```

### Step 10: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_fake_civic_domain_unchanged -v`
Expected: PASS

### Step 11: Add test for existing query params preserved

Add to `tests/analytics/test_utm.py`:

```python
def test_preserves_existing_query_params(self):
    """civic_url tag preserves existing query parameters."""
    template = Template(
        "{% load utm %}"
        '{% civic_url "https://civic.band/page?foo=bar&baz=qux" '
        'medium="search" campaign="test" %}'
    )
    result = template.render(Context({}))

    assert "foo=bar" in result
    assert "baz=qux" in result
    assert "utm_source=civicobserver" in result
```

### Step 12: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_preserves_existing_query_params -v`
Expected: PASS

### Step 13: Add test for empty URL

Add to `tests/analytics/test_utm.py`:

```python
def test_empty_url_returns_empty(self):
    """civic_url tag returns empty string for empty URL."""
    template = Template(
        "{% load utm %}" '{% civic_url "" medium="search" campaign="test" %}'
    )
    result = template.render(Context({}))

    assert result == ""
```

### Step 14: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_empty_url_returns_empty -v`
Expected: PASS

### Step 15: Add test for default content value

Add to `tests/analytics/test_utm.py`:

```python
def test_default_content_value(self):
    """civic_url tag uses 'link' as default content value."""
    template = Template(
        "{% load utm %}"
        '{% civic_url "https://civic.band/page" '
        'medium="search" campaign="test" %}'
    )
    result = template.render(Context({}))

    assert "utm_content=link" in result
```

### Step 16: Run test to verify it passes

Run: `uv run pytest tests/analytics/test_utm.py::TestCivicUrlTag::test_default_content_value -v`
Expected: PASS

### Step 17: Add test for template variable URL

Add to `tests/analytics/test_utm.py`:

```python
def test_url_from_template_variable(self):
    """civic_url tag works with URLs from template variables."""
    template = Template(
        "{% load utm %}"
        '{% civic_url page_url medium="notebook" campaign="notebook_detail" %}'
    )
    result = template.render(
        Context({"page_url": "https://oakland.ca.civic.band/meetings/minutes/456"})
    )

    assert "utm_source=civicobserver" in result
    assert "utm_medium=notebook" in result
    assert "utm_campaign=notebook_detail" in result
```

### Step 18: Run all UTM tests

Run: `uv run pytest tests/analytics/test_utm.py -v`
Expected: All tests PASS

### Step 19: Commit

```bash
git add analytics/templatetags/utm.py tests/analytics/test_utm.py
git commit -m "feat(analytics): add civic_url template tag for UTM tracking

Adds a Django template tag that appends UTM parameters to civic ecosystem
URLs (civic.band, docs.civic.band) for attribution analytics.

- utm_source: always 'civicobserver'
- utm_medium: type of interaction (search, notebook, email, nav)
- utm_campaign: feature context (search_results, notebook_detail, etc.)
- utm_content: UI element (view_button, link, etc.)"
```

---

## Task 2: Update Search Results Template

**Files:**
- Modify: `templates/meetings/partials/search_results.html:128`

### Step 1: Update the "View on CivicBand" link

In `templates/meetings/partials/search_results.html`, find line 128 (the civic.band link) and update:

Before:
```html
<a href="https://{{ result.document.municipality.subdomain }}.civic.band/meetings/{{ result.document.civic_band_table_name }}/{{ result.id }}"
   target="_blank"
   rel="noopener noreferrer"
```

After:
```html
{% load utm %}
...
<a href="{% civic_url 'https://'|add:result.document.municipality.subdomain|add:'.civic.band/meetings/'|add:result.document.civic_band_table_name|add:'/'|add:result.id medium='search' campaign='search_results' content='view_button' %}"
   target="_blank"
   rel="noopener noreferrer"
```

Note: The `{% load utm %}` should be added at the top of the file, after the existing `{% load ... %}` statement.

### Step 2: Verify template renders correctly

Run: `uv run pytest tests/searches/ -v -k "search"`
Expected: PASS (existing tests should still pass)

### Step 3: Commit

```bash
git add templates/meetings/partials/search_results.html
git commit -m "feat(search): add UTM tracking to search result civic.band links"
```

---

## Task 3: Update Notebook Detail Template

**Files:**
- Modify: `templates/notebooks/notebook_detail.html:102`

### Step 1: Update the "View on CivicBand" link

In `templates/notebooks/notebook_detail.html`, add `{% load utm %}` at line 1 and update line 102:

Before:
```html
<a href="https://{{ entry.meeting_page.document.municipality.subdomain }}.civic.band/meetings/{{ entry.meeting_page.document.civic_band_table_name }}/{{ entry.meeting_page.id }}"
   target="_blank"
   rel="noopener noreferrer"
```

After:
```html
{% extends "base.html" %}
{% load utm %}
...
<a href="{% civic_url 'https://'|add:entry.meeting_page.document.municipality.subdomain|add:'.civic.band/meetings/'|add:entry.meeting_page.document.civic_band_table_name|add:'/'|add:entry.meeting_page.id medium='notebook' campaign='notebook_detail' content='view_button' %}"
   target="_blank"
   rel="noopener noreferrer"
```

### Step 2: Verify template renders correctly

Run: `uv run pytest tests/notebooks/ -v`
Expected: PASS

### Step 3: Commit

```bash
git add templates/notebooks/notebook_detail.html
git commit -m "feat(notebooks): add UTM tracking to notebook civic.band links"
```

---

## Task 4: Update Clip Templates

**Files:**
- Modify: `templates/clip/clip.html:33`
- Modify: `templates/clip/partials/page_preview.html:30,135`
- Modify: `templates/clip/partials/error.html:9`

### Step 1: Update clip.html "Return to civic.band" link

In `templates/clip/clip.html`, add `{% load utm %}` after line 2 (after `{% load analytics %}`) and update line 33:

Before:
```html
<a href="https://civic.band"
   class="text-indigo-600 hover:text-indigo-800 font-medium">
    Return to civic.band
</a>
```

After:
```html
<a href="{% civic_url 'https://civic.band' medium='clip' campaign='clip_missing_params' content='return_link' %}"
   class="text-indigo-600 hover:text-indigo-800 font-medium">
    Return to civic.band
</a>
```

### Step 2: Update page_preview.html "View on civic.band" link (line 30)

In `templates/clip/partials/page_preview.html`, add `{% load utm %}` at line 1 (before `{% load analytics %}`) and update line 30:

Before:
```html
<a href="https://{{ subdomain }}.civic.band/{{ table }}/{{ page.document.meeting_name }}/{{ page.document.meeting_date|date:'Y-m-d' }}"
   target="_blank"
   rel="noopener noreferrer"
```

After:
```html
<a href="{% civic_url 'https://'|add:subdomain|add:'.civic.band/'|add:table|add:'/'|add:page.document.meeting_name|add:'/'|add:page.document.meeting_date|date:'Y-m-d' medium='clip' campaign='clip_preview' content='view_link' %}"
   target="_blank"
   rel="noopener noreferrer"
```

### Step 3: Update page_preview.html "Cancel" link (line 135)

Before:
```html
<a href="https://{{ subdomain }}.civic.band"
   class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50">
    Cancel
</a>
```

After:
```html
<a href="{% civic_url 'https://'|add:subdomain|add:'.civic.band' medium='clip' campaign='clip_preview' content='cancel_button' %}"
   class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50">
    Cancel
</a>
```

### Step 4: Update error.html "Return to civic.band" link

In `templates/clip/partials/error.html`, add `{% load utm %}` at line 1 and update line 9:

Before:
```html
<a href="https://{{ subdomain }}.civic.band"
   class="text-indigo-600 hover:text-indigo-800 font-medium">
    Return to civic.band
</a>
```

After:
```html
{% load utm %}
...
<a href="{% civic_url 'https://'|add:subdomain|add:'.civic.band' medium='clip' campaign='clip_error' content='return_link' %}"
   class="text-indigo-600 hover:text-indigo-800 font-medium">
    Return to civic.band
</a>
```

### Step 5: Run clip tests

Run: `uv run pytest tests/clip/ -v`
Expected: PASS

### Step 6: Commit

```bash
git add templates/clip/clip.html templates/clip/partials/page_preview.html templates/clip/partials/error.html
git commit -m "feat(clip): add UTM tracking to clip civic.band links"
```

---

## Task 5: Update Base Template Footer

**Files:**
- Modify: `templates/base.html:172,182,187`

### Step 1: Add utm load to base.html

At line 1, add `{% load utm %}` to the existing load statement:

Before:
```html
{% load analytics static tailwind_cli %}
```

After:
```html
{% load analytics static tailwind_cli utm %}
```

### Step 2: Update CivicBand footer link (line 172)

Before:
```html
<a href="https://civic.band" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    CivicBand
</a>
```

After:
```html
<a href="{% civic_url 'https://civic.band' medium='nav' campaign='footer' content='civicband_link' %}" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    CivicBand
</a>
```

### Step 3: Update Terms of Service link (line 182)

Before:
```html
<a href="https://civic.band/terms" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    Terms of Service
</a>
```

After:
```html
<a href="{% civic_url 'https://civic.band/terms' medium='nav' campaign='footer' content='terms_link' %}" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    Terms of Service
</a>
```

### Step 4: Update Privacy Policy link (line 187)

Before:
```html
<a href="https://civic.band/privacy" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    Privacy Policy
</a>
```

After:
```html
<a href="{% civic_url 'https://civic.band/privacy' medium='nav' campaign='footer' content='privacy_link' %}" class="text-base text-gray-400 hover:text-white transition-colors duration-200" target="_blank" rel="noopener">
    Privacy Policy
</a>
```

### Step 5: Run a quick smoke test

Run: `uv run pytest -v -k "test_" --maxfail=3`
Expected: Tests pass (template changes shouldn't break functionality)

### Step 6: Commit

```bash
git add templates/base.html
git commit -m "feat(nav): add UTM tracking to footer civic.band links"
```

---

## Task 6: Update Email Template

**Files:**
- Modify: `templates/email/search_update.html:169,199`

### Step 1: Add utm load to email template

At line 1, add:

Before:
```html
{% load i18n %}
```

After:
```html
{% load i18n utm %}
```

### Step 2: Update meeting link in email (line 169)

This is trickier because the URL is complex. The email template builds a civic.band URL with query params.

Before:
```html
<a href="https://{{ page.document.municipality.subdomain }}.civic.band/meetings/{% if page.document.document_type == 'agenda' %}agendas{% else %}minutes{% endif %}?meeting={{ page.document.meeting_name|urlencode }}&date={{ page.document.meeting_date|date:'Y-m-d' }}">
```

For email templates with complex URLs, we need a different approach. Build the URL in steps:

After:
```html
<a href="{% civic_url 'https://'|add:page.document.municipality.subdomain|add:'.civic.band/meetings/'|add:page.document.civic_band_table_name|add:'?meeting='|add:page.document.meeting_name|urlencode|add:'&date='|add:page.document.meeting_date|date:'Y-m-d' medium='email' campaign='search_update' content='result_link' %}">
```

Note: This is getting unwieldy. A simpler approach is to leave the URL as-is and just add UTM params at the end manually in the email template, OR create a helper that builds civic.band meeting URLs.

**Alternative approach for emails:** Since email templates are complex, consider leaving the UTM as manual params appended:

```html
<a href="https://{{ page.document.municipality.subdomain }}.civic.band/meetings/{% if page.document.document_type == 'agenda' %}agendas{% else %}minutes{% endif %}?meeting={{ page.document.meeting_name|urlencode }}&date={{ page.document.meeting_date|date:'Y-m-d' }}&utm_source=civicobserver&utm_medium=email&utm_campaign=search_update&utm_content=result_link">
```

This is more readable and maintainable for email templates.

### Step 3: Update civic.observer reference link (line 199)

The link at line 199 points to civic.observer, not civic.band, so no UTM needed there.

### Step 4: Commit

```bash
git add templates/email/search_update.html
git commit -m "feat(email): add UTM tracking to search update email civic.band links"
```

---

## Task 7: Run Full Test Suite and Verify

### Step 1: Run all tests

Run: `uv run pytest -v`
Expected: All tests PASS

### Step 2: Run linting

Run: `uv run --group dev ruff check .`
Expected: No errors

### Step 3: Run type checking

Run: `uv run --group dev mypy .`
Expected: No errors (or only pre-existing ones)

### Step 4: Manual verification (optional)

Start the dev server and verify:
1. Search for something, click "View on CivicBand" - URL should have UTM params
2. Open a notebook, click "View on CivicBand" - URL should have UTM params
3. Check footer links - should have UTM params

### Step 5: Final commit if any fixes needed

```bash
git add -A
git commit -m "fix: address any issues from testing"
```

---

## Summary

**Files created:**
- `analytics/templatetags/utm.py` - The template tag
- `tests/analytics/test_utm.py` - Unit tests

**Files modified:**
- `templates/meetings/partials/search_results.html` - Search results
- `templates/notebooks/notebook_detail.html` - Notebook detail
- `templates/clip/clip.html` - Clip main page
- `templates/clip/partials/page_preview.html` - Clip preview
- `templates/clip/partials/error.html` - Clip error
- `templates/base.html` - Footer links
- `templates/email/search_update.html` - Email notifications
