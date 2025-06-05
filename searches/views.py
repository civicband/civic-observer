from django.db.models import QuerySet
from neapolitan.views import CRUDView

from .models import SavedSearch


class SavedSearchCRUDView(CRUDView):
    model = SavedSearch
    url_base = "searches:savedsearch"
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
