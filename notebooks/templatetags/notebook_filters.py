from django import template

register = template.Library()


@register.filter
def in_set(value, the_set):
    """Check if value is in a set."""
    if the_set is None:
        return False
    return value in the_set
