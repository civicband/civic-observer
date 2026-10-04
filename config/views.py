import logging

from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def homepage(request):
    """Homepage view for CivicObserver."""
    context = {
        "title": "CivicObserver",
        "description": "Empowering civic engagement through transparency and observation",
    }
    return render(request, "homepage.html", context)


def health_check(request):
    try:
        for conn in connections.all():
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
    except Exception:
        logger.warning("Health check failed", exc_info=True)
        return JsonResponse({"status": "unhealthy"}, status=503)

    return JsonResponse({"status": "ok"})


def api_page(request):
    """API information page for researchers and developers."""
    return render(request, "api.html")
