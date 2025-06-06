from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.generic import View
from neapolitan.views import CRUDView

from .models import SavedSearch


class SavedSearchCRUDView(CRUDView):
    model = SavedSearch
    url_base = "searches:savedsearch"  # type: ignore
    fields = ["name", "search"]
    list_display = ["name", "search", "created"]
    search_fields = ["name", "search__search_term", "search__muni__name"]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[SavedSearch]:
        return SavedSearch.objects.filter(user=self.request.user).select_related(
            "search", "search__muni"
        )

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)


class SavedSearchEmailPreviewView(View):
    """Staff-only view to preview the email that would be sent for a saved search."""

    def get(self, request, pk, format="html"):
        # Get the saved search, ensuring staff access
        saved_search = get_object_or_404(SavedSearch, pk=pk)

        # Prepare the context
        context = {"subscription": saved_search}

        if format == "txt":
            # Render plain text email
            content = render_to_string("email/search_update.txt", context=context)
            return HttpResponse(content, content_type="text/plain; charset=utf-8")
        else:
            # Render HTML email
            content = render_to_string("email/search_update.html", context=context)
            return HttpResponse(content, content_type="text/html; charset=utf-8")


# Apply staff_member_required decorator
saved_search_email_preview = staff_member_required(
    SavedSearchEmailPreviewView.as_view()
)
