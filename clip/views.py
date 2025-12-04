from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ClipView(LoginRequiredMixin, TemplateView):
    """
    Clip endpoint for saving meeting pages from civic.band to notebooks.

    Accepts query params:
    - id: civic.band page ID
    - subdomain: municipality subdomain
    - table: "agendas" or "minutes"
    """

    template_name = "clip/clip.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_id"] = self.request.GET.get("id", "")
        context["subdomain"] = self.request.GET.get("subdomain", "")
        context["table"] = self.request.GET.get("table", "")
        return context
