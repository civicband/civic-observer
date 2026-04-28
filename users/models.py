from django.contrib.auth.models import AbstractUser
from django.db import models


def get_timezone_choices():
    """Get sorted IANA timezone choices for the timezone field."""
    from zoneinfo import available_timezones

    return sorted(available_timezones())


class User(AbstractUser):
    email = models.EmailField(unique=True)
    analytics_opt_out = models.BooleanField(
        default=False,
        help_text="Exclude this user from analytics tracking (admin-only)",
    )
    timezone = models.CharField(
        max_length=63,
        default="America/New_York",
        help_text="IANA timezone for scheduling digest emails",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
