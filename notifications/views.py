from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView

from .forms import NotificationChannelForm
from .models import NotificationChannel


class ChannelListView(LoginRequiredMixin, ListView):
    """List user's notification channels."""

    model = NotificationChannel
    template_name = "notifications/channel_list.html"
    context_object_name = "channels"

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)  # type: ignore[misc]


class ChannelCreateView(LoginRequiredMixin, CreateView):
    """Create a new notification channel."""

    model = NotificationChannel
    form_class = NotificationChannelForm
    template_name = "notifications/partials/channel_form.html"
    success_url = reverse_lazy("notifications:channel-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()

        # Return HTMX response
        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "notifications/partials/channel_row.html",
                {"channel": self.object},
                request=self.request,
            )
            return HttpResponse(html)

        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "notifications/partials/channel_form.html",
                {"form": form},
                request=self.request,
            )
            return HttpResponse(html)
        return super().form_invalid(form)


class ChannelDeleteView(LoginRequiredMixin, DeleteView):  # type: ignore[misc]
    """Delete a notification channel."""

    model = NotificationChannel
    success_url = reverse_lazy("notifications:channel-list")

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)  # type: ignore[misc]

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()  # type: ignore[misc]
        self.object.delete()

        if request.headers.get("HX-Request"):
            return HttpResponse("")

        return super().delete(request, *args, **kwargs)
