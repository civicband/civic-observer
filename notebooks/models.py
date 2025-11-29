import uuid

from django.conf import settings
from django.db import models
from model_utils.models import TimeStampedModel


class Notebook(TimeStampedModel):
    """
    A collection of saved meeting pages belonging to a user.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notebooks",
    )
    name = models.CharField(max_length=200)
    is_archived = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Notebook"
        verbose_name_plural = "Notebooks"
        ordering = ["-modified"]

    def __str__(self) -> str:
        return self.name


class Tag(TimeStampedModel):
    """
    A tag for categorizing notebook entries.
    User-scoped now, team-scoped in future.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
        null=True,
        blank=True,
    )
    # Future: team = models.ForeignKey("teams.Team", null=True, blank=True, ...)

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "user"],
                name="unique_tag_per_user",
            ),
        ]

    def __str__(self) -> str:
        return self.name
