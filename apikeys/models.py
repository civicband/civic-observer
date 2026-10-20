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
    all_sites = models.BooleanField(
        default=True,
        help_text="Allow this key to access every civic.band site",
    )
    sites = models.ManyToManyField(  # type: ignore[var-annotated]
        "municipalities.Muni",
        through="APIKeySite",
        related_name="api_keys",
        blank=True,
        help_text="Sites this key may access when all_sites is false",
    )

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
    def create_key(
        cls,
        name: str,
        user=None,
        expires_at=None,
        all_sites: bool = True,
        sites=None,
    ) -> tuple[APIKey, str]:
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
            all_sites=all_sites,
        )
        if sites:
            instance.sites.set(sites)
        return instance, raw_key

    def is_valid(self) -> bool:
        """Check if key is currently valid."""
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def get_site_for_subdomain(self, subdomain: str):
        """Return the Muni this key may access for a subdomain, if any.

        All-sites keys return the matching Muni when it exists; site-scoped
        keys only return a Muni that is explicitly linked.
        """
        from municipalities.models import Muni

        if self.all_sites:
            return Muni.objects.filter(subdomain=subdomain).first()
        return self.sites.filter(subdomain=subdomain).first()

    def allows_subdomain(self, subdomain: str) -> bool:
        """Check whether this key may access a civic.band subdomain."""
        if self.all_sites:
            return True
        return self.sites.filter(subdomain=subdomain).exists()

    def site_summary(self) -> str:
        """Human-readable summary of the sites this key can access."""
        if self.all_sites:
            return "All sites"
        subdomains = [site.subdomain for site in self.sites.all()]
        return ", ".join(subdomains) if subdomains else "None"


class APIKeySite(TimeStampedModel):
    """Explicit link between an API key and a civic.band site.

    The explicit through model leaves room for per-site throttle settings
    without another model migration.
    """

    api_key = models.ForeignKey(
        APIKey, on_delete=models.CASCADE, related_name="site_links"
    )
    muni = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="api_key_links",
    )

    class Meta:
        verbose_name = "API Key Site"
        verbose_name_plural = "API Key Sites"
        constraints = [
            models.UniqueConstraint(
                fields=["api_key", "muni"],
                name="unique_api_key_muni",
            )
        ]

    def __str__(self):
        return f"{self.api_key.name} -> {self.muni.subdomain}"
