import uuid

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
