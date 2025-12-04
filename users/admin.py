from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.shortcuts import redirect, render
from django.urls import path
from stagedoor.helpers import email_login_link
from stagedoor.models import generate_token

from .models import User


class InviteUserForm(forms.Form):
    """Simple form for inviting a user by email."""

    email = forms.EmailField(
        label="Email address",
        help_text="Enter the email address of the user you want to invite.",
    )


@staff_member_required
def invite_user_view(request):
    """Admin view to invite a new user by sending them a login email."""
    if request.method == "POST":
        form = InviteUserForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            token = generate_token(email=email)
            if token:
                email_login_link(request, token)
                messages.success(request, f"Invitation sent to {email}")
                return redirect("admin:invite_user")
            else:
                messages.error(request, f"Could not generate invite for {email}")
    else:
        form = InviteUserForm()

    context = {
        "form": form,
        "title": "Invite User",
        "site_header": admin.site.site_header,
        "site_title": admin.site.site_title,
        "has_permission": True,
    }
    return render(request, "admin/users/invite_user.html", context)


# Register the custom admin URL by wrapping get_urls
_original_get_urls = admin.site.get_urls


def _get_urls_with_invite():
    custom_urls = [
        path("invite/", invite_user_view, name="invite_user"),
    ]
    return custom_urls + _original_get_urls()


admin.site.get_urls = _get_urls_with_invite  # type: ignore[method-assign]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "first_name", "last_name", "is_staff"]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
        "analytics_opt_out",
    ]
    search_fields = ["email", "username", "first_name", "last_name"]
    ordering = ["email"]

    # Add analytics_opt_out to the permissions fieldset
    fieldsets = (
        *(BaseUserAdmin.fieldsets or ()),
        ("Analytics", {"fields": ("analytics_opt_out",)}),
    )
