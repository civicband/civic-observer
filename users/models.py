from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    analytics_opt_out = models.BooleanField(
        default=False,
        help_text="Exclude this user from analytics tracking (admin-only)",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
