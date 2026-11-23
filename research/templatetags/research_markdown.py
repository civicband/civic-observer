"""Render memo Markdown safely."""

from django import template
from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt

register = template.Library()

_renderer = MarkdownIt("commonmark", {"html": False})


@register.filter
def research_markdown(value: str) -> str:
    return mark_safe(_renderer.render(value or ""))


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
