from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, UpdateView, View
from neapolitan.views import CRUDView

from municipalities.models import Muni

from .forms import SavedSearchCreateForm, SavedSearchEditForm
from .models import SavedSearch, Search


class SavedSearchCRUDView(CRUDView):
    model = SavedSearch
    url_base = "searches:savedsearch"  # type: ignore
    fields = ["name", "search"]
    list_display = ["name", "search", "created"]
    search_fields = ["name", "search__search_term"]

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[SavedSearch]:
        return (
            SavedSearch.objects.filter(user=self.request.user)
            .select_related("search")
            .prefetch_related("search__municipalities")
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

    def get_initial(self):
        """Pre-fill municipality from query parameter."""
        initial = super().get_initial()
        municipality_id = self.request.GET.get("municipality")
        if municipality_id:
            try:
                initial["municipality"] = Muni.objects.get(pk=municipality_id)
            except (Muni.DoesNotExist, ValueError):
                pass
        return initial

    def form_valid(self, form):
        # Authentication check
        if not self.request.user.is_authenticated:
            return self.form_invalid(form)

        # Save the form to create/get Search object and create SavedSearch
        self.object = form.save(user=self.request.user)

        # Check if user already has another SavedSearch pointing to the same Search
        # This happens if they try to save the same search parameters twice
        existing_saved_search = (
            SavedSearch.objects.filter(
                user=self.request.user,
                search=self.object.search,
            )
            .exclude(pk=self.object.pk)
            .first()
        )

        if existing_saved_search:
            # Delete the one we just created and show error
            self.object.delete()
            form.add_error(
                None,
                f"You already have a saved search for this: {existing_saved_search.name}",
            )
            return self.form_invalid(form)

        return redirect(self.success_url)  # type: ignore


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
            "search"
        )

    def form_valid(self, form):
        # Authentication check
        if not self.request.user.is_authenticated:
            return self.form_invalid(form)

        # Check for duplicate saved search with same underlying Search object
        # Since SearchManager.get_or_create_for_params now returns existing searches,
        # we just need to check if user already saved that exact Search
        # The form's save() method will get_or_create the Search object
        # We need to check after that if user already has a SavedSearch for it
        self.object = form.save(user=self.request.user)

        # Check if user already has another SavedSearch pointing to the same Search
        existing_saved_search = (
            SavedSearch.objects.filter(
                user=self.request.user,
                search=self.object.search,
            )
            .exclude(pk=self.object.pk)
            .first()
        )

        if existing_saved_search:
            # Delete the one we just created and show error
            self.object.delete()
            form.add_error(
                None,
                f"You already have a saved search for this: {existing_saved_search.name}",
            )
            return self.form_invalid(form)

        return redirect(self.success_url)  # type: ignore


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


@login_required
@require_POST
def save_search_from_params(request):
    """
    Save a search from page search parameters.

    Accepts POST data with:
    - name: Name for the saved search
    - notification_frequency: immediate, daily, or weekly
    - query: Search term (optional, empty = all updates mode)
    - municipalities: List of municipality IDs
    - states: List of state codes
    - date_from: Start date (optional)
    - date_to: End date (optional)
    - document_type: Document type filter
    - meeting_name_query: Meeting name filter (optional)
    """
    import json
    from datetime import datetime

    try:
        # Parse POST data (may be JSON or form data)
        if request.content_type == "application/json":
            data = json.loads(request.body)
        else:
            data = request.POST

        # Get saved search metadata
        name = data.get("name", "").strip()
        notification_frequency = data.get("notification_frequency", "immediate")

        # Validate required fields
        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)

        if notification_frequency not in ["immediate", "daily", "weekly"]:
            return JsonResponse({"error": "Invalid notification frequency"}, status=400)

        # Get search parameters
        search_term = data.get("query", "").strip()
        municipality_ids = (
            data.getlist("municipalities")
            if hasattr(data, "getlist")
            else data.get("municipalities", [])
        )
        states = (
            data.getlist("states")
            if hasattr(data, "getlist")
            else data.get("states", [])
        )
        date_from_str = data.get("date_from", "").strip()
        date_to_str = data.get("date_to", "").strip()
        document_type = data.get("document_type", "all")
        meeting_name_query = data.get("meeting_name_query", "").strip()

        # Parse dates
        date_from = None
        date_to = None
        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse({"error": "Invalid date_from format"}, status=400)

        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse({"error": "Invalid date_to format"}, status=400)

        # Get municipalities
        municipalities = []
        if municipality_ids:
            if not isinstance(municipality_ids, list):
                municipality_ids = [municipality_ids]
            municipalities = list(Muni.objects.filter(id__in=municipality_ids))

        # Get or create Search object
        search = Search.objects.get_or_create_for_params(
            search_term=search_term,
            municipalities=municipalities,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
        )

        # Check if user already has this search saved
        existing = SavedSearch.objects.filter(user=request.user, search=search).first()

        if existing:
            return JsonResponse(
                {
                    "error": f'You already have this search saved as "{existing.name}"',
                    "existing_id": str(existing.id),
                },
                status=400,
            )

        # Create SavedSearch
        saved_search = SavedSearch.objects.create(
            user=request.user,
            search=search,
            name=name,
            notification_frequency=notification_frequency,
        )

        return JsonResponse(
            {
                "success": True,
                "saved_search_id": str(saved_search.id),
                "message": f'Search "{name}" saved successfully!',
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
