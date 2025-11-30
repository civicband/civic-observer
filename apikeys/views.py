from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import ListView

from .forms import APIKeyCreateForm
from .models import APIKey


class APIKeyListView(LoginRequiredMixin, ListView):
    model = APIKey
    template_name = "apikeys/apikey_list.html"
    context_object_name = "api_keys"

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user)  # type: ignore[misc]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = APIKeyCreateForm()
        return context


class APIKeyCreateView(LoginRequiredMixin, View):
    """HTMX endpoint to create a new API key."""

    def post(self, request):
        form = APIKeyCreateForm(request.POST)
        if form.is_valid():
            api_key, raw_key = APIKey.create_key(
                name=form.cleaned_data["name"],
                user=request.user,
                expires_at=form.cleaned_data.get("expires_at"),
            )
            html = render_to_string(
                "apikeys/partials/key_created_modal.html",
                {"api_key": api_key, "raw_key": raw_key},
                request=request,
            )
            return HttpResponse(html)

        html = render_to_string(
            "apikeys/partials/create_form.html",
            {"form": form},
            request=request,
        )
        return HttpResponse(html, status=400)


class APIKeyRevokeView(LoginRequiredMixin, View):
    """Revoke (deactivate) an API key."""

    def post(self, request, pk):
        api_key = get_object_or_404(APIKey, pk=pk, user=request.user)
        api_key.is_active = False
        api_key.save()
        return redirect("apikeys:list")


class APIKeyDeleteView(LoginRequiredMixin, View):
    """Permanently delete an API key."""

    def post(self, request, pk):
        api_key = get_object_or_404(APIKey, pk=pk, user=request.user)
        api_key.delete()
        return redirect("apikeys:list")


class APIKeyDownloadView(LoginRequiredMixin, View):
    """One-time download of newly created key (from session)."""

    def get(self, request):
        raw_key = request.session.pop("new_api_key", None)
        if not raw_key:
            return HttpResponse("Key no longer available", status=404)

        response = HttpResponse(raw_key, content_type="text/plain")
        response["Content-Disposition"] = 'attachment; filename="civicband-api-key.txt"'
        return response
