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
