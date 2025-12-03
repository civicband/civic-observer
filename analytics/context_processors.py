from django.conf import settings
from django.http import HttpRequest


def umami_context(request: HttpRequest) -> dict:
    """Inject Umami analytics configuration into template context."""
    enabled = getattr(settings, "UMAMI_ENABLED", False)
    opted_out = False

    if hasattr(request, "user") and request.user.is_authenticated:
        opted_out = getattr(request.user, "analytics_opt_out", False)

    # Respect Do Not Track header
    dnt = request.headers.get("dnt") == "1"

    return {
        "umami_enabled": enabled and not dnt,
        "umami_opted_out": opted_out,
        "umami_website_id": getattr(settings, "UMAMI_WEBSITE_ID", ""),
        "umami_script_url": getattr(settings, "UMAMI_SCRIPT_URL", ""),
    }
