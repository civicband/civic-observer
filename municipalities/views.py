import json
import logging
import os

from django.db.models import Count
from django.http import JsonResponse
from django.views import View
from neapolitan.views import CRUDView

from .filters import MuniFilter
from .models import Muni

logger = logging.getLogger(__name__)


class MuniCRUDView(CRUDView):
    model = Muni
    url_base = "munis:muni"  # type: ignore
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
    paginate_by = 25

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .annotate(saved_searches_count=Count("searches__saved_by", distinct=True))
        )

        # Apply filters
        self.filterset = MuniFilter(self.request.GET, queryset=queryset)
        queryset = self.filterset.qs

        # Handle sorting for HTMX requests
        sort_by = self.request.GET.get("sort", "name")
        sort_order = self.request.GET.get("order", "asc")

        # Define allowed sort fields with their database field names
        sort_fields = {
            "name": "name",
            "state": "state",
            "kind": "kind",
            "pages": "pages",
            "saved_searches": "saved_searches_count",
            "last_updated": "last_updated",
        }

        if sort_by in sort_fields:
            order_field = sort_fields[sort_by]
            if sort_order == "desc":
                order_field = f"-{order_field}"
            queryset = queryset.order_by(order_field)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filterset"] = getattr(self, "filterset", None)
        context["total_count"] = Muni.objects.count()
        return context

    def list(self, request, *args, **kwargs):
        """Override to handle HTMX requests for sorting."""
        # For HTMX requests, return just the table body
        if request.headers.get("HX-Request"):
            self.object_list = self.get_queryset()
            context = self.get_context_data()
            from django.template.loader import render_to_string

            html = render_to_string(
                "municipalities/partials/muni_table_body.html", context, request=request
            )
            from django.http import HttpResponse

            return HttpResponse(html)

        # For regular requests, use the default behavior
        return super().list(request, *args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        # Check if this is a protected operation (create, update, delete)
        # Based on the role passed in the view configuration
        if hasattr(self, "role") and self.role.name in ["CREATE", "UPDATE", "DELETE"]:
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)


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
            return False
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

        # Get old page count before update to detect changes
        try:
            old_muni = Muni.objects.get(subdomain=subdomain)
            old_pages = old_muni.pages
        except Muni.DoesNotExist:
            old_pages = None

        muni, created = Muni.objects.update_or_create(
            subdomain=subdomain, defaults=muni_data
        )
        muni.update_searches()

        # Only backfill if this is a new municipality OR if the page count changed
        new_pages = muni.pages
        should_backfill = created or (old_pages is not None and old_pages != new_pages)

        if should_backfill:
            # Backfill meeting data from civic.band asynchronously
            try:
                import django_rq

                from meetings.tasks import backfill_municipality_meetings_task

                queue = django_rq.get_queue("default")
                job = queue.enqueue(backfill_municipality_meetings_task, muni.id)
                reason = (
                    "new municipality"
                    if created
                    else f"pages changed ({old_pages} → {new_pages})"
                )
                logger.info(
                    f"Enqueued backfill task for {subdomain} ({reason}, job ID: {job.id})"
                )
            except Exception as e:
                # Log the error but don't fail the webhook
                logger.error(
                    f"Failed to enqueue backfill task for {subdomain}: {e}",
                    exc_info=True,
                )
        else:
            logger.info(
                f"Skipping backfill for {subdomain}: page count unchanged ({new_pages})"
            )

        # Prepare response data
        response_data = {
            "id": str(muni.id),
            "subdomain": muni.subdomain,
            "name": muni.name,
            "state": muni.state,
            "country": str(muni.country),
            "kind": muni.kind,
            "pages": muni.pages,
            "last_updated": muni.last_updated if muni.last_updated else None,
            "latitude": muni.latitude,
            "longitude": muni.longitude,
            "popup_data": muni.popup_data,
            "created": muni.created,
            "modified": muni.modified,
            "action": "created" if created else "updated",
        }

        status_code = 201 if created else 200
        return JsonResponse(response_data, status=status_code)
