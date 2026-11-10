from django.apps import AppConfig


class NotebooksConfig(AppConfig):
    """Retained for migration history; the models were removed in Release 2."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notebooks"
