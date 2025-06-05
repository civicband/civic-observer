import uuid

from django.conf import settings
from django.db import models
from model_utils.models import TimeStampedModel

from municipalities.models import Muni


class Search(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    muni = models.ForeignKey(Muni, on_delete=models.CASCADE, related_name="searches")
    search_term = models.CharField(max_length=500, blank=True)
    all_results = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Search"
        verbose_name_plural = "Searches"
        ordering = ["-created"]

    def __str__(self) -> str:
        if self.search_term:
            return f"Search for '{self.search_term}' in {self.muni.name}"
        return f"Search in {self.muni.name} (all results: {self.all_results})"


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

    class Meta:
        verbose_name = "Saved Search"
        verbose_name_plural = "Saved Searches"
        ordering = ["-created"]
        unique_together = ["user", "search"]

    def __str__(self) -> str:
        return f"{self.name} - {self.user.email}"
