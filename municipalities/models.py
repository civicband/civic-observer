import uuid

from django.db import models
from django_countries.fields import CountryField
from localflavor.ca.ca_provinces import PROVINCE_CHOICES
from localflavor.us.us_states import STATE_CHOICES
from model_utils.models import TimeStampedModel


class Muni(TimeStampedModel):
    STATE_FIELD_CHOICES = STATE_CHOICES + PROVINCE_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subdomain = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=10, choices=STATE_FIELD_CHOICES)
    country = CountryField(max_length=255, default="US")
    kind = models.CharField(max_length=255)
    pages = models.IntegerField(default=0)
    last_updated = models.DateTimeField(null=True, blank=True)
    last_indexed = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of when meeting documents were last indexed for this municipality",
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    popup_data = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "Municipality"
        verbose_name_plural = "Municipalities"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}, {self.state}"

    def update_searches(self) -> None:
        # Prefetch saved_by and user to avoid N+1 queries in update_search()
        for search in self.searches.prefetch_related("saved_by__user").all():  # type: ignore
            search.update_search()
