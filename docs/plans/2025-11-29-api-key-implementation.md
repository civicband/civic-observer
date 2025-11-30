# API Key Implementation Plan for civic.observer

## Overview

Add API key management to civic.observer with:
1. APIKey model for storing keys
2. User-facing UI at `/api-keys` for key management
3. Internal endpoint for corkboard to validate keys (Tailscale-only)

## Task Breakdown

### Task 1: Create apikeys Django app

Create new Django app with model and admin.

**Files to create:**
- `apikeys/__init__.py`
- `apikeys/apps.py`
- `apikeys/models.py`
- `apikeys/admin.py`
- `apikeys/urls.py`
- `apikeys/views.py`
- `apikeys/forms.py`

**Model definition (`apikeys/models.py`):**
```python
import hashlib
import secrets
from django.conf import settings
from django.db import models
from django_extensions.db.models import TimeStampedModel
import uuid


class APIKey(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="api_keys",
    )
    name = models.CharField(max_length=255, help_text="A label for this key")
    prefix = models.CharField(
        max_length=16, db_index=True, help_text="First chars for identification"
    )
    key_hash = models.CharField(
        max_length=64, unique=True, help_text="SHA256 hash of the key"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"
        ordering = ["-created"]

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new API key with prefix."""
        random_part = secrets.token_hex(16)  # 32 chars
        return f"cb_live_{random_part}"

    @classmethod
    def hash_key(cls, key: str) -> str:
        """Hash a key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def create_key(cls, name: str, user=None, expires_at=None) -> tuple["APIKey", str]:
        """Create a new API key. Returns (instance, raw_key)."""
        raw_key = cls.generate_key()
        prefix = raw_key[:16]  # "cb_live_" + first 8 random chars
        key_hash = cls.hash_key(raw_key)

        instance = cls.objects.create(
            name=name,
            user=user,
            prefix=prefix,
            key_hash=key_hash,
            expires_at=expires_at,
        )
        return instance, raw_key

    def is_valid(self) -> bool:
        """Check if key is currently valid."""
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True
```

**Register in `config/settings/base.py`:**
Add `"apikeys"` to `INSTALLED_APPS`.

**Verification:** Run `uv run python manage.py makemigrations apikeys` and `uv run python manage.py migrate`

---

### Task 2: Add admin interface

**File: `apikeys/admin.py`**
```python
from django.contrib import admin
from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix", "user", "is_active", "created", "last_used_at"]
    list_filter = ["is_active", "created"]
    search_fields = ["name", "prefix", "user__email"]
    readonly_fields = ["prefix", "key_hash", "created", "modified", "last_used_at"]
    raw_id_fields = ["user"]
```

**Verification:** Access `/admin/apikeys/apikey/` and confirm it loads.

---

### Task 3: Create user-facing views

**File: `apikeys/views.py`**
```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
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
        return APIKey.objects.filter(user=self.request.user)

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
```

**File: `apikeys/forms.py`**
```python
from django import forms


class APIKeyCreateForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                "placeholder": "e.g., Production Server",
            }
        ),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
            }
        ),
    )
```

**File: `apikeys/urls.py`**
```python
from django.urls import path
from . import views

app_name = "apikeys"

urlpatterns = [
    path("", views.APIKeyListView.as_view(), name="list"),
    path("create/", views.APIKeyCreateView.as_view(), name="create"),
    path("<uuid:pk>/revoke/", views.APIKeyRevokeView.as_view(), name="revoke"),
    path("<uuid:pk>/delete/", views.APIKeyDeleteView.as_view(), name="delete"),
    path("download/", views.APIKeyDownloadView.as_view(), name="download"),
]
```

**Update `config/urls.py`:**
Add `path("api-keys/", include("apikeys.urls")),`

**Verification:** Navigate to `/api-keys/` while logged in.

---

### Task 4: Create templates

**File: `templates/apikeys/apikey_list.html`**
```html
{% extends "base.html" %}

{% block title %}API Keys - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <div class="sm:flex sm:items-center sm:justify-between">
        <div>
            <h1 class="text-3xl font-bold tracking-tight text-gray-900">API Keys</h1>
            <p class="mt-2 text-sm text-gray-600">Manage API keys for accessing CivicBand data</p>
        </div>
        <div class="mt-4 sm:mt-0">
            <button type="button"
                    @click="$dispatch('open-create-modal')"
                    class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700">
                Create API Key
            </button>
        </div>
    </div>

    {% if api_keys %}
    <div class="mt-8 overflow-hidden shadow ring-1 ring-black ring-opacity-5 rounded-lg">
        <table class="min-w-full divide-y divide-gray-300">
            <thead class="bg-gray-50">
                <tr>
                    <th class="py-3.5 pl-4 pr-3 text-left text-sm font-semibold text-gray-900">Name</th>
                    <th class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Key</th>
                    <th class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Created</th>
                    <th class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Last Used</th>
                    <th class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Status</th>
                    <th class="relative py-3.5 pl-3 pr-4"><span class="sr-only">Actions</span></th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 bg-white">
                {% for key in api_keys %}
                <tr>
                    <td class="whitespace-nowrap py-4 pl-4 pr-3 text-sm font-medium text-gray-900">{{ key.name }}</td>
                    <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500 font-mono">{{ key.prefix }}...</td>
                    <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">{{ key.created|date:"M d, Y" }}</td>
                    <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                        {% if key.last_used_at %}{{ key.last_used_at|timesince }} ago{% else %}Never{% endif %}
                    </td>
                    <td class="whitespace-nowrap px-3 py-4 text-sm">
                        {% if key.is_active %}
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Active</span>
                        {% else %}
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">Revoked</span>
                        {% endif %}
                    </td>
                    <td class="relative whitespace-nowrap py-4 pl-3 pr-4 text-right text-sm font-medium">
                        {% if key.is_active %}
                        <form method="post" action="{% url 'apikeys:revoke' key.pk %}" class="inline">
                            {% csrf_token %}
                            <button type="submit" class="text-yellow-600 hover:text-yellow-900">Revoke</button>
                        </form>
                        {% endif %}
                        <form method="post" action="{% url 'apikeys:delete' key.pk %}" class="inline ml-3">
                            {% csrf_token %}
                            <button type="submit" class="text-red-600 hover:text-red-900">Delete</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="mt-8 text-center py-12 bg-white rounded-lg border border-gray-200">
        <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"></path>
        </svg>
        <h3 class="mt-4 text-lg font-medium text-gray-900">No API keys yet</h3>
        <p class="mt-2 text-gray-500">Create an API key to access CivicBand data programmatically.</p>
    </div>
    {% endif %}
</div>

<!-- Create Modal -->
<div x-data="{ open: false }"
     @open-create-modal.window="open = true"
     @keydown.escape.window="open = false"
     x-show="open"
     x-cloak
     class="fixed inset-0 z-50 overflow-y-auto">
    <div class="flex min-h-full items-end justify-center p-4 text-center sm:items-center sm:p-0">
        <div x-show="open" x-transition class="fixed inset-0 bg-gray-500 bg-opacity-75" @click="open = false"></div>
        <div x-show="open" x-transition class="relative transform overflow-hidden rounded-lg bg-white px-4 pb-4 pt-5 text-left shadow-xl sm:my-8 sm:w-full sm:max-w-lg sm:p-6">
            <div id="modal-content">
                {% include "apikeys/partials/create_form.html" %}
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

**File: `templates/apikeys/partials/create_form.html`**
```html
<form hx-post="{% url 'apikeys:create' %}"
      hx-target="#modal-content"
      hx-swap="innerHTML">
    {% csrf_token %}
    <h3 class="text-lg font-semibold text-gray-900 mb-4">Create API Key</h3>

    <div class="space-y-4">
        <div>
            <label for="id_name" class="block text-sm font-medium text-gray-700">Name</label>
            <div class="mt-1">{{ form.name }}</div>
            <p class="mt-1 text-xs text-gray-500">A label to help you identify this key</p>
        </div>

        <div>
            <label for="id_expires_at" class="block text-sm font-medium text-gray-700">Expires (optional)</label>
            <div class="mt-1">{{ form.expires_at }}</div>
        </div>
    </div>

    <div class="mt-6 flex justify-end gap-3">
        <button type="button" @click="open = false" class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50">Cancel</button>
        <button type="submit" class="px-4 py-2 text-sm font-medium text-white bg-indigo-600 border border-transparent rounded-md hover:bg-indigo-700">Create Key</button>
    </div>
</form>
```

**File: `templates/apikeys/partials/key_created_modal.html`**
```html
<div class="text-center">
    <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
        <svg class="h-6 w-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
        </svg>
    </div>
    <h3 class="mt-4 text-lg font-semibold text-gray-900">API Key Created</h3>
    <p class="mt-2 text-sm text-red-600 font-medium">Copy this key now. You won't be able to see it again!</p>

    <div class="mt-4 p-3 bg-gray-100 rounded-md">
        <code id="api-key-value" class="text-sm font-mono break-all">{{ raw_key }}</code>
    </div>

    <div class="mt-4 flex justify-center gap-3">
        <button type="button"
                onclick="navigator.clipboard.writeText('{{ raw_key }}'); this.textContent = 'Copied!'"
                class="px-4 py-2 text-sm font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-md hover:bg-indigo-100">
            Copy to Clipboard
        </button>
        <a href="{% url 'apikeys:download' %}"
           class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50">
            Download as File
        </a>
    </div>

    <div class="mt-6">
        <a href="{% url 'apikeys:list' %}" class="text-sm text-indigo-600 hover:text-indigo-800">Done</a>
    </div>
</div>
```

**Verification:** Create a key, verify modal shows, copy works, download works.

---

### Task 5: Create internal validation endpoint

**File: `apikeys/internal_views.py`**
```python
import json
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings

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
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
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
```

**Update `apikeys/urls.py`:**
```python
from django.urls import path
from . import views
from . import internal_views

app_name = "apikeys"

urlpatterns = [
    path("", views.APIKeyListView.as_view(), name="list"),
    path("create/", views.APIKeyCreateView.as_view(), name="create"),
    path("<uuid:pk>/revoke/", views.APIKeyRevokeView.as_view(), name="revoke"),
    path("<uuid:pk>/delete/", views.APIKeyDeleteView.as_view(), name="delete"),
    path("download/", views.APIKeyDownloadView.as_view(), name="download"),
]

# Internal API endpoints
internal_urlpatterns = [
    path("validate-key", internal_views.ValidateKeyView.as_view(), name="validate-key"),
]
```

**Update `config/urls.py`:**
```python
from apikeys.urls import internal_urlpatterns as apikeys_internal

urlpatterns = [
    # ... existing patterns ...
    path("api-keys/", include("apikeys.urls")),
    path("api/v1/", include((apikeys_internal, "apikeys_internal"))),
]
```

**Add to settings:**
```python
# In config/settings/base.py
CORKBOARD_SERVICE_SECRET = env("CORKBOARD_SERVICE_SECRET", default="")
```

**Verification:** Test with curl from Tailscale machine:
```bash
curl -X POST http://civic-observer/api/v1/validate-key \
  -H "Content-Type: application/json" \
  -H "X-Service-Secret: $SECRET" \
  -d '{"api_key": "cb_live_..."}'
```

---

### Task 6: Add navigation link

**Update `templates/base.html`:**
Add "API Keys" link in user menu for authenticated users.

**Verification:** Link appears and navigates correctly.

---

### Task 7: Write tests

**File: `tests/apikeys/test_models.py`**
- Test key generation format
- Test key hashing
- Test is_valid with active/inactive/expired keys

**File: `tests/apikeys/test_views.py`**
- Test list view requires auth
- Test create key
- Test revoke key
- Test delete key

**File: `tests/apikeys/test_internal_views.py`**
- Test rejects non-Tailscale IPs
- Test rejects missing/wrong secret
- Test validates valid key
- Test rejects invalid key
- Test updates last_used_at

**Verification:** `uv run pytest tests/apikeys/ -v`

---

### Task 8: Fix download flow

Update `APIKeyCreateView` to store key in session for download:
```python
def post(self, request):
    # ... after creating key ...
    request.session["new_api_key"] = raw_key
    # ... return modal ...
```

**Verification:** Create key, click download, verify file contents.

---

## Environment Variables to Add

```bash
# .env
CORKBOARD_SERVICE_SECRET=<generate-a-secure-random-string>
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Create apikeys Django app with model |
| 2 | Add admin interface |
| 3 | Create user-facing views |
| 4 | Create templates |
| 5 | Create internal validation endpoint |
| 6 | Add navigation link |
| 7 | Write tests |
| 8 | Fix download flow |
