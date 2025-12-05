from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render


def homepage(request):
    """Homepage view for CivicObserver."""
    context = {
        "title": "CivicObserver",
        "description": "Empowering civic engagement through transparency and observation",
    }
    return render(request, "homepage.html", context)


def health_check(request):
    db_ok = all(conn.cursor().execute("SELECT 1") for conn in connections.all())
    # TODO: Add cache check if there is ever caching
    status = db_ok
    status_code = 200 if status else 503
    return JsonResponse({"status": "ok" if status else "unhealthy"}, status=status_code)


def api_page(request):
    """API information page for researchers and developers."""
    return render(request, "api.html")
