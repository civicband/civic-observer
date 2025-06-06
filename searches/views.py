from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, UpdateView, View
from neapolitan.views import CRUDView

from municipalities.models import Muni

from .forms import SavedSearchCreateForm, SavedSearchEditForm
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


class SavedSearchCreateView(CreateView):
    """Custom create view for saved searches using search parameters."""

    model = SavedSearch
    form_class = SavedSearchCreateForm
    template_name = "searches/savedsearch_create.html"
    success_url = reverse_lazy("searches:savedsearch-list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Check if user already has this search saved
        municipality = form.cleaned_data["municipality"]
        search_term = form.cleaned_data.get("search_term", "").strip()
        all_results = form.cleaned_data.get("all_results", False)

        # Check for existing saved search with same parameters
        if not self.request.user.is_authenticated:
            return self.form_invalid(form)

        existing_saved_search = SavedSearch.objects.filter(
            user=self.request.user,
            search__muni=municipality,
            search__search_term=search_term,
            search__all_results=all_results,
        ).first()

        if existing_saved_search:
            form.add_error(
                None,
                f"You already have a saved search for this: {existing_saved_search.name}",
            )
            return self.form_invalid(form)

        # Save the form with the current user
        self.object = form.save(user=self.request.user)
        return redirect(self.success_url)


class SavedSearchEditView(UpdateView):
    """Custom edit view for saved searches using search parameters."""

    model = SavedSearch
    form_class = SavedSearchEditForm
    template_name = "searches/savedsearch_edit.html"
    success_url = reverse_lazy("searches:savedsearch-list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # Ensure users can only edit their own saved searches
        if not self.request.user.is_authenticated:
            return SavedSearch.objects.none()

        return SavedSearch.objects.filter(user=self.request.user).select_related(
            "search", "search__muni"
        )

    def form_valid(self, form):
        # Check if user already has this search saved (excluding current object)
        municipality = form.cleaned_data["municipality"]
        search_term = form.cleaned_data.get("search_term", "").strip()
        all_results = form.cleaned_data.get("all_results", False)

        # Check for existing saved search with same parameters
        if not self.request.user.is_authenticated:
            return self.form_invalid(form)

        existing_saved_search = (
            SavedSearch.objects.filter(
                user=self.request.user,
                search__muni=municipality,
                search__search_term=search_term,
                search__all_results=all_results,
            )
            .exclude(pk=self.object.pk)
            .first()
        )

        if existing_saved_search:
            form.add_error(
                None,
                f"You already have a saved search for this: {existing_saved_search.name}",
            )
            return self.form_invalid(form)

        # Save the form with the current user
        self.object = form.save(user=self.request.user)
        return redirect(self.success_url)


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


@require_GET
def municipality_search(request):
    """HTMX endpoint for searching municipalities"""
    query = request.GET.get("q", "").strip()
    selected_id = request.GET.get("selected", "")

    if not query:
        municipalities = Muni.objects.all()[:10]
    else:
        municipalities = Muni.objects.filter(
            Q(name__icontains=query) | Q(state__icontains=query)
        )[:10]

    html = render_to_string(
        "searches/partials/municipality_options.html",
        {
            "municipalities": municipalities,
            "selected_id": selected_id,
            "query": query,
        },
        request=request,
    )

    return HttpResponse(html)
