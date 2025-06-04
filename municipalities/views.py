from neapolitan.views import CRUDView

from .models import Muni


class MuniCRUDView(CRUDView):
    model = Muni
    fields = [
        "subdomain",
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "last_updated",
        "latitude",
        "longitude",
        "popup_data",
    ]
    list_display = ["name", "state", "kind", "pages", "last_updated"]
    search_fields = ["name", "subdomain", "state"]
    filterset_fields = ["state", "kind", "country"]
