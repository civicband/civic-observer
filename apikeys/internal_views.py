import hmac
import json
from datetime import timedelta
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import APIKey

# Only touch last_used_at if the key hasn't been validated within this window,
# so a hot key doesn't cause a DB write on every single request.
LAST_USED_UPDATE_INTERVAL = timedelta(minutes=5)

MAX_URL_LENGTH = 8192

# Tailscale CGNAT plus loopback and private ranges. The shared secret is still
# required; this list only limits where the call can originate.
SERVICE_NETWORKS = (
    ip_network("100.64.0.0/10"),  # Tailscale CGNAT
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
)


def is_service_ip(ip: str) -> bool:
    """Check if an IP may call the internal service endpoints."""
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    return any(addr in network for network in SERVICE_NETWORKS)


def get_client_ip(request) -> str:
    """Return the client IP, trusting only the proxy-appended hop.

    A reverse proxy appends the real peer address to ``X-Forwarded-For``, so
    the *last* entry is the only one we can trust. Taking the first entry would
    let a caller spoof the service allowlist by sending their own header.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def _service_auth_error(request):
    """Return an error response when the caller is not a trusted service."""
    if not is_service_ip(get_client_ip(request)):
        return JsonResponse({"error": "Forbidden"}, status=403)

    expected_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", None)
    provided_secret = request.headers.get("X-Service-Secret") or ""
    if not expected_secret or not hmac.compare_digest(provided_secret, expected_secret):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    return None


def _parse_body(request):
    """Parse a JSON object body, returning None for anything else."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def _valid_key(api_key: str) -> APIKey | None:
    """Return the matching key if it exists, is active, and is usable."""
    if not api_key or not api_key.startswith("cb_live_"):
        return None

    try:
        key_obj = APIKey.objects.select_related("user").get(
            key_hash=APIKey.hash_key(api_key)
        )
    except APIKey.DoesNotExist:
        return None

    if not key_obj.is_valid():
        return None
    if key_obj.user and not key_obj.user.is_active:
        return None
    return key_obj


def _mark_used(key_obj: APIKey) -> None:
    """Update last_used_at, throttled so hot keys don't write every request."""
    now = timezone.now()
    if (
        key_obj.last_used_at is None
        or now - key_obj.last_used_at >= LAST_USED_UPDATE_INTERVAL
    ):
        APIKey.objects.filter(pk=key_obj.pk).update(last_used_at=now)


def _configured_domains() -> list[str]:
    configured = getattr(settings, "CIVIC_BAND_DOMAINS", ["civic.band"])
    if isinstance(configured, str):
        return [d.strip().lower() for d in configured.split(",") if d.strip()]
    return [str(d).strip().lower() for d in configured if str(d).strip()]


def validate_api_url(url: str) -> tuple[bool, str | None]:
    """Check that a URL is a civic.band subdomain JSON endpoint.

    Returns ``(True, subdomain)`` when allowed, otherwise ``(False, None)``.
    The URL is only inspected, never fetched.
    """
    if not url or len(url) > MAX_URL_LENGTH or any(c.isspace() for c in url):
        return False, None

    try:
        parsed = urlsplit(url)
    except ValueError:
        return False, None

    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False, None

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname or not parsed.path.endswith(".json"):
        return False, None

    for domain in _configured_domains():
        suffix = f".{domain}"
        if hostname.endswith(suffix):
            subdomain = hostname[: -len(suffix)]
            if subdomain:
                return True, subdomain

    return False, None


@method_decorator(csrf_exempt, name="dispatch")
class AuthorizeView(View):
    """Internal endpoint for corkboard to judge URL + API key access."""

    def post(self, request):
        auth_error = _service_auth_error(request)
        if auth_error:
            return auth_error

        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid request"}, status=400)

        api_key = data.get("api_key")
        url = data.get("url")
        if (
            not isinstance(api_key, str)
            or not isinstance(url, str)
            or not api_key
            or not url
        ):
            return JsonResponse({"error": "Invalid request"}, status=400)

        valid_url, subdomain = validate_api_url(url)
        if not valid_url or subdomain is None:
            return JsonResponse({"allowed": False, "reason": "invalid_url"})

        key_obj = _valid_key(api_key)
        if key_obj is None:
            return JsonResponse({"allowed": False, "reason": "invalid_key"})

        site = key_obj.get_site_for_subdomain(subdomain)
        if not key_obj.all_sites and site is None:
            return JsonResponse({"allowed": False, "reason": "site_not_allowed"})

        _mark_used(key_obj)

        response_data = {
            "allowed": True,
            "reason": "ok",
            "key_id": str(key_obj.id),
            "subdomain": subdomain,
        }
        if site:
            response_data["site_id"] = str(site.id)
        if key_obj.user:
            response_data["user_id"] = str(key_obj.user.id)
            response_data["user_email"] = key_obj.user.email

        return JsonResponse(response_data)


@method_decorator(csrf_exempt, name="dispatch")
class ValidateKeyView(View):
    """Backwards-compatible internal endpoint for validating API keys."""

    def post(self, request):
        auth_error = _service_auth_error(request)
        if auth_error:
            return auth_error

        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid request"}, status=400)

        api_key = data.get("api_key", "")
        if not isinstance(api_key, str):
            api_key = ""

        key_obj = _valid_key(api_key)
        if key_obj is None:
            return JsonResponse({"valid": False})

        _mark_used(key_obj)

        response_data = {
            "valid": True,
            "key_id": str(key_obj.id),
        }
        if key_obj.user:
            response_data["user_id"] = str(key_obj.user.id)
            response_data["user_email"] = key_obj.user.email

        return JsonResponse(response_data)
