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
