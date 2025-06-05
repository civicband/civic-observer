from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods


@login_required
@require_http_methods(["GET"])
def datasette_auth(request):
    """Return user authentication data for Datasette integration."""
    user = request.user
    return JsonResponse(
        {
            "id": user.id,
            "name": user.email,
        }
    )
