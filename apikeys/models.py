import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from model_utils.models import TimeStampedModel


class APIKey(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="api_keys",
    )
    name = models.CharField(max_length=255, help_text="A label for this key")
    prefix = models.CharField(
        max_length=16, db_index=True, help_text="First chars for identification"
    )
    key_hash = models.CharField(
        max_length=64, unique=True, help_text="SHA256 hash of the key"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"
        ordering = ["-created"]

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new API key with prefix."""
        random_part = secrets.token_hex(16)  # 32 chars
        return f"cb_live_{random_part}"

    @classmethod
    def hash_key(cls, key: str) -> str:
        """Hash a key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def create_key(cls, name: str, user=None, expires_at=None) -> tuple["APIKey", str]:
        """Create a new API key. Returns (instance, raw_key)."""
        raw_key = cls.generate_key()
        prefix = raw_key[:16]  # "cb_live_" + first 8 random chars
        key_hash = cls.hash_key(raw_key)

        instance = cls.objects.create(
            name=name,
            user=user,
            prefix=prefix,
            key_hash=key_hash,
            expires_at=expires_at,
        )
        return instance, raw_key

    def is_valid(self) -> bool:
        """Check if key is currently valid."""
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True
