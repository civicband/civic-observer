import uuid

import httpx
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import models
from django.template.loader import get_template, render_to_string
from django.utils import timezone
from model_utils.models import TimeStampedModel

from municipalities.models import Muni


class SearchManager(models.Manager):
    def get_or_create_for_params(self, muni, search_term="", all_results=False):
        """Get or create a Search object for the given parameters."""
        # Normalize search_term
        search_term = search_term.strip() if search_term else ""

        return self.get_or_create(
            muni=muni,
            search_term=search_term,
            all_results=all_results,
        )


class Search(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    muni = models.ForeignKey(Muni, on_delete=models.CASCADE, related_name="searches")
    search_term = models.CharField(max_length=500, blank=True)
    all_results = models.BooleanField(default=False)
    last_fetched = models.DateTimeField(null=True, blank=True)
    last_agenda_matched = models.DateTimeField(null=True, blank=True)
    last_minutes_matched = models.DateTimeField(null=True, blank=True)
    agenda_match_json = models.JSONField(null=True, blank=True)
    minutes_match_json = models.JSONField(null=True, blank=True)

    objects = SearchManager()

    class Meta:
        verbose_name = "Search"
        verbose_name_plural = "Searches"
        ordering = ["-created"]
        unique_together = ["muni", "search_term", "all_results"]

    def __str__(self) -> str:
        if self.search_term:
            return f"Search for '{self.search_term}' in {self.muni.name}"
        return f"Search in {self.muni.name} (all results: {self.all_results})"

    def update_search(self) -> None:
        # Implement logic to update search results based on the search_term and muni
        subdomain = self.muni.subdomain
        agenda_updated = False
        minutes_updated = False

        if self.all_results:
            agendas_query = "/meetings/-/query.json?sql=select+distinct+meeting%2C+date%2C+count(page)+from+agendas+where+date+>%3D+current_date+group+by+meeting%2C+date+order+by+date+asc"
            minutes_query = "/meetings/-/query?sql=select+distinct+meeting%2C+date%2C+count(page)+from+minutes+where+date+<%3D+current_date+group+by+meeting%2C+date+order+by+date+desc"
        if self.search_term:
            agendas_query = f"/meetings/-/query?sql=select+id%2C+meeting%2C+date%2C+page%2C+text%2C+page_image+from+agendas+where+rowid+in+(select+rowid+from+agendas_fts+where+agendas_fts+match+escape_fts(%3Asearch))+and+date+>%3D+current_date+order+by+date+asc&search={self.search_term.replace(' ', '+')}"
            minutes_query = f"/meetings/-/query?sql=select+id%2C+meeting%2C+date%2C+page%2C+text%2C+page_image+from+minutes+where+rowid+in+(select+rowid+from+minutes_fts+where+minutes_fts+match+escape_fts(%3Asearch))+and+date+<%3D+current_date+order+by+date+desc&search={self.search_term.replace(' ', '+')}"
        agendas_query_url = f"https://{subdomain}.civic.band{agendas_query}"
        minutes_query_url = f"https://{subdomain}.civic.band{minutes_query}"
        agendas_resp = httpx.get(agendas_query_url)
        minutes_resp = httpx.get(minutes_query_url)
        if (
            agendas_resp.status_code == 200
            and len(agendas_resp.json().get("rows", [])) > 0
        ):
            if agendas_resp.json()["rows"] != self.agenda_match_json:
                self.agenda_match_json = agendas_resp.json()["rows"]
                self.last_agenda_matched = timezone.now()
                agenda_updated = True
        if (
            minutes_resp.status_code == 200
            and len(minutes_resp.json().get("rows", [])) > 0
        ):
            if minutes_resp.json()["rows"] != self.minutes_match_json:
                self.minutes_match_json = minutes_resp.json()["rows"]
                self.last_minutes_matched = timezone.now()
                minutes_updated = True

        # Save changes before sending notifications
        self.save()

        # Send notifications if there were updates
        if agenda_updated or minutes_updated:
            for saved_search in self.saved_by.all():  # type: ignore
                saved_search.send_search_notification()


class SavedSearch(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_searches",
    )
    search = models.ForeignKey(
        Search, on_delete=models.CASCADE, related_name="saved_by"
    )
    name = models.CharField(max_length=200, help_text="A name for this saved search")
    last_notification_sent = models.DateTimeField(
        null=True, blank=True, help_text="When the last notification email was sent"
    )

    class Meta:
        verbose_name = "Saved Search"
        verbose_name_plural = "Saved Searches"
        ordering = ["-created"]
        unique_together = ["user", "search"]

    def __str__(self) -> str:
        return f"{self.name} - {self.user.email}"

    def send_search_notification(self) -> None:
        context = {"subscription": self}
        txt_content = render_to_string("email/search_update.txt", context=context)
        html_content = get_template("email/search_update.html").render(context=context)
        msg = EmailMultiAlternatives(
            subject=f"New Results for {self.search}",
            to=[self.user.email],
            from_email="Civic Observer <noreply@civic.observer>",
            body=txt_content,
        )
        msg.attach_alternative(html_content, "text/html")
        msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore

        msg.send()

        # Update the last notification sent timestamp
        self.last_notification_sent = timezone.now()
        self.save(update_fields=["last_notification_sent"])
