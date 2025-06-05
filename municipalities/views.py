import json
import os

from django.db.models import Count
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from neapolitan.views import CRUDView

from .models import Muni


class MuniCRUDView(CRUDView):
    model = Muni
    url_base = (
        "munis:muni"  # This tells neapolitan what URL pattern to use for redirects
    )
    fields = [
        "subdomain",
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "last_updated",
        "latitude",
        "longitude",
        "popup_data",
    ]
    list_display = [
        "name",
        "state",
        "kind",
        "pages",
        "last_updated",
        "saved_searches_count",
    ]
    search_fields = ["name", "subdomain", "state"]
    filterset_fields = ["state", "kind", "country"]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(saved_searches_count=Count("searches__saved_by", distinct=True))
        )

    def dispatch(self, request, *args, **kwargs):
        # Check if this is a protected operation (create, update, delete)
        # Based on the role passed in the view configuration
        if hasattr(self, "role") and self.role.name in ["CREATE", "UPDATE", "DELETE"]:
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
class MuniWebhookUpdateView(View):
    """
    API endpoint for updating/creating municipalities via webhook.
    Accepts PUT/POST requests with optional webhook secret authentication.
    Creates a new municipality if subdomain doesn't exist, updates if it does.
    """

    def authenticate_webhook(self, request):
        """Check webhook secret if environment variable is set"""
        webhook_secret = os.environ.get("WEBHOOK_SECRET")
        if not webhook_secret:
            return True  # No authentication required if secret not set

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return False

        # Support both "Bearer <token>" and direct token formats
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = auth_header

        return token == webhook_secret

    def dispatch(self, request, *args, **kwargs):
        if not self.authenticate_webhook(request):
            return JsonResponse({"error": "Invalid webhook secret"}, status=401)
        return super().dispatch(request, *args, **kwargs)

    def put(self, request, subdomain):
        return self._update_or_create(request, subdomain)

    def post(self, request, subdomain):
        return self._update_or_create(request, subdomain)

    def _update_or_create(self, request, subdomain):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Ensure subdomain in data matches URL parameter
        data["subdomain"] = subdomain

        # Extract valid model fields
        valid_fields = {f.name for f in Muni._meta.fields if f.name != "id"}
        muni_data = {k: v for k, v in data.items() if k in valid_fields}

        if not muni_data.get("name"):
            return JsonResponse({"error": "name field is required"}, status=400)

        try:
            muni, created = Muni.objects.update_or_create(
                subdomain=subdomain, defaults=muni_data
            )

            # Prepare response data
            response_data = {
                "id": str(muni.id),
                "subdomain": muni.subdomain,
                "name": muni.name,
                "state": muni.state,
                "country": muni.country,
                "kind": muni.kind,
                "pages": muni.pages,
                "last_updated": muni.last_updated.isoformat()
                if muni.last_updated
                else None,
                "latitude": muni.latitude,
                "longitude": muni.longitude,
                "popup_data": muni.popup_data,
                "created": muni.created.isoformat(),
                "modified": muni.modified.isoformat(),
                "action": "created" if created else "updated",
            }

            status_code = 201 if created else 200
            return JsonResponse(response_data, status=status_code)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
