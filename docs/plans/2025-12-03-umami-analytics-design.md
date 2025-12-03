# Umami Analytics Integration Design

## Overview

Add comprehensive Umami analytics tracking to CivicObserver to understand user behavior, feature adoption, and engagement. The integration tracks page views automatically and key user actions via custom events.

## Configuration

- **Umami Server**: https://analytics.civic.band/
- **Script Source**: https://analytics.civic.band/sunshine
- **Website ID**: 522b42fb-2e46-4ba3-9803-4e17c7824958

## Architecture

### Components

1. **Django Settings** - Configuration for Umami (enabled/disabled, URLs, website ID)
2. **Context Processor** - Injects tracking config into all templates
3. **Template Filter** - `track_event` filter for adding tracking attributes to elements
4. **JavaScript Module** - Handles HTMX event tracking
5. **User Model Field** - Admin-controlled opt-out preference

### User Identification Strategy

- **Anonymous visitors**: Standard Umami tracking (no identifier)
- **Authenticated users**: `data-tag` attribute set to user ID for correlation
- **DNT Respect**: Browser Do Not Track header is honored

### Opt-Out Mechanism

- Environment-level: `UMAMI_ENABLED` setting (False in dev/test, True in production)
- User-level: `analytics_opt_out` field on User model (admin-controlled only)
- Browser-level: DNT header respected via `data-do-not-track="true"`

## Implementation Details

### Django Settings

```python
# config/settings/base.py
UMAMI_ENABLED = False
UMAMI_WEBSITE_ID = "522b42fb-2e46-4ba3-9803-4e17c7824958"
UMAMI_SCRIPT_URL = "https://analytics.civic.band/sunshine"

# config/settings/production.py
UMAMI_ENABLED = True
```

### Context Processor

```python
# analytics/context_processors.py
def umami_context(request):
    enabled = getattr(settings, "UMAMI_ENABLED", False)
    opted_out = False

    if request.user.is_authenticated:
        opted_out = getattr(request.user, "analytics_opt_out", False)

    dnt = request.META.get("HTTP_DNT") == "1"

    return {
        "umami_enabled": enabled and not dnt,
        "umami_opted_out": opted_out,
        "umami_website_id": getattr(settings, "UMAMI_WEBSITE_ID", ""),
        "umami_script_url": getattr(settings, "UMAMI_SCRIPT_URL", ""),
    }
```

### Template Filter

```python
# analytics/templatetags/analytics.py
from django import template

register = template.Library()


@register.filter
def track_event(event_name):
    """Output Umami tracking attribute for an event."""
    return f'data-umami-event="{event_name}"'


@register.filter
def track_event_data(event_name, data):
    """Output Umami tracking attribute with event data."""
    return f'data-umami-event="{event_name}" data-umami-event-data="{data}"'
```

### Base Template Script

```html
{% if umami_enabled and not umami_opted_out %}
<script defer src="{{ umami_script_url }}"
        data-website-id="{{ umami_website_id }}"
        data-do-not-track="true"
        {% if user.is_authenticated %}data-tag="{{ user.id }}"{% endif %}>
</script>
<script src="{% static 'js/analytics.js' %}" defer></script>
{% endif %}
```

### JavaScript Module

```javascript
// static/js/analytics.js
(function() {
    if (typeof umami === 'undefined') return;

    document.body.addEventListener('htmx:beforeRequest', function(evt) {
        const el = evt.detail.elt;
        const event = el.dataset.umamiHtmxEvent;
        if (event) {
            umami.track(event);
        }
    });
})();
```

### Template Filter Usage

```html
<!-- Simple event -->
<button {{ "save_search"|track_event }}>Save Search</button>

<!-- Event with data -->
<a href="..." {{ "muni_viewed"|track_event_data:municipality.slug }}>
    {{ municipality.name }}
</a>

<!-- Navigation with property -->
<a href="..." data-umami-event="nav_click" data-umami-event-destination="municipalities">
    Municipalities
</a>

<!-- HTMX event -->
<input type="search"
       hx-get="{% url 'meetings:meeting-search' %}"
       data-umami-htmx-event="search_query">
```

### User Model Addition

```python
# users/models.py
analytics_opt_out = models.BooleanField(
    default=False, help_text="Exclude this user from analytics tracking"
)
```

## Events to Track

### Navigation
- `nav_click` - with `data-umami-event-destination` property
- `mobile_menu_open`

### Authentication
- `login_start`
- `login_complete`
- `logout`

### Search Behavior
- `search_query` (HTMX)
- `search_filter_applied`
- `search_result_clicked`
- `search_saved`

### Saved Searches
- `saved_search_created`
- `saved_search_edited`
- `saved_search_deleted`
- `saved_search_viewed`

### Notebooks
- `notebook_created`
- `notebook_deleted`
- `page_saved_to_notebook`
- `page_removed_from_notebook`

### Notifications
- `notification_channel_created`
- `notification_channel_deleted`
- `notification_channel_toggled`

### API Keys
- `api_key_created`
- `api_key_revoked`

### Municipality
- `muni_viewed` - with municipality slug as event data

## File Structure

### New App: `analytics/`

```
analytics/
├── __init__.py
├── apps.py
├── context_processors.py
└── templatetags/
    ├── __init__.py
    └── analytics.py
```

### Files to Modify

- `config/settings/base.py` - Add Umami settings
- `config/settings/production.py` - Enable Umami
- `users/models.py` - Add `analytics_opt_out` field
- `users/admin.py` - Expose opt-out in admin
- `templates/base.html` - Add Umami script and analytics.js
- `static/js/analytics.js` - Create HTMX tracking module

### Templates to Instrument

- `templates/base.html` - Navigation links
- `templates/meetings/meeting_search.html` - Search inputs and results
- `templates/searches/savedsearch_*.html` - CRUD actions
- `templates/notebooks/notebook_*.html` - CRUD actions
- `templates/notifications/channel_*.html` - Channel management
- `templates/apikeys/apikey_list.html` - Key creation/revocation
- `templates/municipalities/muni_detail.html` - Municipality viewing
