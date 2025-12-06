# UTM Tracking for Outbound Links

## Overview

Add UTM tracking parameters to civic ecosystem outbound links for attribution analytics, user journey tracking, and campaign measurement.

## Goals

1. **Attribution analytics** - Know which pages/features on civic.observer drive traffic to civic.band
2. **User journey tracking** - Understand complete user flow when users navigate between properties
3. **Campaign measurement** - Distinguish traffic sources for marketing campaigns

## Solution

### Template Tag: `{% civic_url %}`

A Django template tag that adds UTM parameters to civic ecosystem URLs.

**Location:** `analytics/templatetags/utm.py`

**Syntax:**
```django
{% load utm %}

<a href="{% civic_url page_url medium='search' campaign='search_results' content='view_button' %}">
  View on CivicBand
</a>
```

**Parameters:**
| Parameter | Required | Description | Example Values |
|-----------|----------|-------------|----------------|
| `url` | Yes | The base URL to civic ecosystem | `https://alameda.ca.civic.band/...` |
| `medium` | Yes | Type of interaction | `search`, `notebook`, `clip`, `email`, `nav` |
| `campaign` | Yes | Feature/page context | `search_results`, `notebook_detail`, `clip_preview`, `weekly_digest`, `footer` |
| `content` | No | UI element (default: `link`) | `view_button`, `document_link`, `header_link` |

**Example output:**
```
https://alameda.ca.civic.band/meetings/agendas/abc123?utm_source=civicobserver&utm_medium=search&utm_campaign=search_results&utm_content=view_button
```

### UTM Parameter Structure

| Parameter | Value | Notes |
|-----------|-------|-------|
| `utm_source` | `civicobserver` | Always this value |
| `utm_medium` | varies | Type of interaction |
| `utm_campaign` | varies | Feature/page context |
| `utm_content` | varies | UI element clicked |

### Tracked Domains

The tag adds UTM params only to civic ecosystem domains:
- `civic.band` (including subdomains like `alameda.ca.civic.band`)
- `docs.civic.band`

Non-civic URLs are returned unchanged.

## Implementation Details

### Domain Allowlist

```python
CIVIC_DOMAINS = [
    "civic.band",
    "docs.civic.band",
]
```

### URL Parsing Logic

1. Parse the URL with `urllib.parse.urlparse()`
2. Check if domain ends with any allowlisted domain (handles subdomains)
3. If match: parse existing query params, add UTM params, rebuild URL
4. If no match: return original URL unchanged

### Edge Cases

| Case | Behavior |
|------|----------|
| URL with existing query params | UTM params appended |
| URL with existing UTM params | Overwritten with new values |
| Relative URL (`/path`) | Returned unchanged |
| None/empty URL | Returns empty string |

### Template Tag Registration

```python
from django import template

register = template.Library()


@register.simple_tag
def civic_url(url, medium, campaign, content="link"): ...
```

## Templates to Update

### High-traffic links (search & notebooks)
- `meetings/partials/search_results.html` - "View on CivicBand" button
- `notebooks/notebook_detail.html` - Links to original source documents
- `clip/clip.html` - Clip editor links back to civic.band
- `clip/partials/page_preview.html` - Preview and view links

### Navigation & footer
- `base.html` - Footer links to civic.band, docs.civic.band, terms, privacy

### Email templates
- `email/search_update.html` - Document links in search notifications
- `email/digest_update.html` - If any civic.band links exist

## Testing

### Unit tests (`analytics/tests/test_utm.py`)

1. **Civic ecosystem URLs get UTM params:**
   - `civic.band` → params added
   - `alameda.ca.civic.band` → params added
   - `docs.civic.band` → params added

2. **Non-civic URLs unchanged:**
   - `google.com` → no params
   - `civic.band.fake.com` → no params

3. **Existing query params preserved:**
   - `civic.band/page?foo=bar` → `civic.band/page?foo=bar&utm_source=...`

4. **Edge cases:**
   - Empty/None URL → returns empty string
   - Relative URL → returned unchanged
   - URL with existing UTM params → overwritten

5. **Template integration test:**
   - Render template using `{% civic_url %}`, verify output

## Not Included (YAGNI)

- JavaScript fallback for missed links
- Admin UI for configuring domains/params
- Analytics dashboard for viewing UTM data (handled by analytics platform)
