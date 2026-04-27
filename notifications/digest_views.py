from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView

from .forms import DigestSubscriptionForm
from .models import DigestSubscription


class DigestSubscriptionListView(LoginRequiredMixin, ListView):
    """List user's daily digest subscriptions."""

    model = DigestSubscription
    template_name = "notifications/digest_list.html"
    context_object_name = "subscriptions"

    def get_queryset(self):
        return (
            DigestSubscription.objects.filter(user=self.request.user)
            .select_related("municipality")
            .order_by("municipality__name")
        )


class DigestSubscriptionCreateView(LoginRequiredMixin, CreateView):
    """Create a new daily digest subscription."""

    model = DigestSubscription
    form_class = DigestSubscriptionForm
    template_name = "notifications/partials/digest_form.html"
    success_url = reverse_lazy("notifications:digest-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()

        # Return HTMX response
        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "notifications/partials/digest_row.html",
                {"subscription": self.object},
                request=self.request,
            )
            return HttpResponse(html)

        return super().form_valid(form)


class DigestSubscriptionDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a daily digest subscription."""

    model = DigestSubscription
    success_url = reverse_lazy("notifications:digest-list")

    def get_queryset(self):
        return DigestSubscription.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()

        if request.headers.get("HX-Request"):
            return HttpResponse("")

        return super().delete(request, *args, **kwargs)
