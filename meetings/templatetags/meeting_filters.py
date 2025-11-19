"""Template filters for meeting search."""

from django import template

register = template.Library()


@register.filter
def rank_label(rank):
    """
    Convert a search rank score to a user-friendly label.

    Args:
        rank: Float value representing search relevance (0.0 to 1.0)

    Returns:
        String label: "High match", "Medium match", or "Low match"
    """
    if rank is None:
        return ""

    try:
        rank_float = float(rank)
    except (ValueError, TypeError):
        return ""

    if rank_float >= 0.05:
        return "High match"
    elif rank_float >= 0.01:
        return "Medium match"
    else:
        return "Low match"


@register.filter
def rank_badge_color(rank):
    """
    Get the appropriate badge color class for a rank score.

    Args:
        rank: Float value representing search relevance

    Returns:
        String with Tailwind CSS classes for badge styling
    """
    if rank is None:
        return "bg-gray-100 text-gray-800"

    try:
        rank_float = float(rank)
    except (ValueError, TypeError):
        return "bg-gray-100 text-gray-800"

    if rank_float >= 0.5:
        return "bg-emerald-100 text-emerald-800"  # High - green
    elif rank_float >= 0.1:
        return "bg-yellow-100 text-yellow-800"  # Medium - yellow
    else:
        return "bg-orange-100 text-orange-800"  # Low - orange
