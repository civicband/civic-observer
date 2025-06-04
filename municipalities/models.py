import uuid

from django.db import models
from model_utils.models import TimeStampedModel


class Muni(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subdomain = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=10)
    country = models.CharField(max_length=50, default="USA")
    kind = models.CharField(max_length=50)
    pages = models.IntegerField(default=0)
    last_updated = models.DateField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    popup_data = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "Municipality"
        verbose_name_plural = "Municipalities"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}, {self.state}"
