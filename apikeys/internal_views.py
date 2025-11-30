import json

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import APIKey


def is_tailscale_ip(ip: str) -> bool:
    """Check if IP is in Tailscale CGNAT range (100.64.0.0/10)."""
    if not ip:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
        # 100.64.0.0/10 = 100.64.0.0 - 100.127.255.255
        return first == 100 and 64 <= second <= 127
    except ValueError:
        return False


def get_client_ip(request) -> str:
    """Get client IP from request, checking X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@method_decorator(csrf_exempt, name="dispatch")
class ValidateKeyView(View):
    """Internal endpoint for corkboard to validate API keys."""

    def post(self, request):
        # Check Tailscale IP
        client_ip = get_client_ip(request)
        if not is_tailscale_ip(client_ip):
            return JsonResponse({"error": "Forbidden"}, status=403)

        # Check shared secret
        expected_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", None)
        provided_secret = request.headers.get("X-Service-Secret")
        if not expected_secret or provided_secret != expected_secret:
            return JsonResponse({"error": "Unauthorized"}, status=401)

        # Parse request body
        try:
            data = json.loads(request.body)
            api_key = data.get("api_key", "")
        except (json.JSONDecodeError, AttributeError):
            return JsonResponse({"error": "Invalid request"}, status=400)

        if not api_key:
            return JsonResponse({"valid": False})

        # Validate key format
        if not api_key.startswith("cb_live_"):
            return JsonResponse({"valid": False})

        # Look up key by hash
        key_hash = APIKey.hash_key(api_key)
        try:
            key_obj = APIKey.objects.get(key_hash=key_hash)
        except APIKey.DoesNotExist:
            return JsonResponse({"valid": False})

        # Check if valid
        if not key_obj.is_valid():
            return JsonResponse({"valid": False})

        # Update last_used_at
        key_obj.last_used_at = timezone.now()
        key_obj.save(update_fields=["last_used_at"])

        # Return success with metadata
        response_data = {
            "valid": True,
            "key_id": str(key_obj.id),
        }
        if key_obj.user:
            response_data["user_id"] = str(key_obj.user.id)
            response_data["user_email"] = key_obj.user.email

        return JsonResponse(response_data)
