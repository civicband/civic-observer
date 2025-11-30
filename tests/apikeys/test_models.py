from datetime import timedelta

import pytest
from django.utils import timezone

from apikeys.models import APIKey
from tests.factories import APIKeyFactory, UserFactory


@pytest.mark.django_db
class TestAPIKeyModel:
    def test_create_api_key(self):
        """Test basic API key creation."""
        user = UserFactory()
        api_key = APIKeyFactory(user=user, name="Production Key")

        assert api_key.name == "Production Key"
        assert api_key.user == user
        assert api_key.is_active is True
        assert api_key.created is not None
        assert api_key.modified is not None
        assert str(api_key.id)  # UUID is valid

    def test_api_key_str_representation(self):
        """Test string representation."""
        api_key = APIKeyFactory(name="Test Key", prefix="cb_live_abc123")
        assert str(api_key) == "Test Key (cb_live_abc123...)"

    def test_api_key_can_be_null_user(self):
        """Test that user can be null for system keys."""
        api_key = APIKeyFactory(user=None, name="System Key")
        assert api_key.user is None
        assert api_key.name == "System Key"

    def test_generate_key_format(self):
        """Test that generated keys have correct format."""
        key = APIKey.generate_key()

        assert key.startswith("cb_live_")
        assert len(key) == 40  # "cb_live_" (8) + 32 hex chars
        # Verify it's valid hex after prefix
        hex_part = key[8:]
        assert len(hex_part) == 32
        int(hex_part, 16)  # Should not raise ValueError

    def test_generate_key_uniqueness(self):
        """Test that generated keys are unique."""
        keys = [APIKey.generate_key() for _ in range(100)]
        assert len(keys) == len(set(keys))  # All unique

    def test_hash_key(self):
        """Test key hashing."""
        key = "cb_live_test1234567890abcdef123456"
        hash1 = APIKey.hash_key(key)
        hash2 = APIKey.hash_key(key)

        assert len(hash1) == 64  # SHA256 hex digest
        assert hash1 == hash2  # Same input produces same hash

    def test_hash_key_different_for_different_keys(self):
        """Test that different keys produce different hashes."""
        key1 = "cb_live_test1234567890abcdef123456"
        key2 = "cb_live_test1234567890abcdef654321"

        hash1 = APIKey.hash_key(key1)
        hash2 = APIKey.hash_key(key2)

        assert hash1 != hash2

    def test_create_key_method(self):
        """Test the create_key class method."""
        user = UserFactory()
        api_key, raw_key = APIKey.create_key(name="Test Key", user=user)

        assert api_key.name == "Test Key"
        assert api_key.user == user
        assert api_key.is_active is True
        assert api_key.expires_at is None
        assert raw_key.startswith("cb_live_")
        assert api_key.prefix == raw_key[:16]
        assert api_key.key_hash == APIKey.hash_key(raw_key)

    def test_create_key_with_expiration(self):
        """Test creating key with expiration date."""
        user = UserFactory()
        expires = timezone.now() + timedelta(days=30)
        api_key, raw_key = APIKey.create_key(
            name="Temporary Key", user=user, expires_at=expires
        )

        assert api_key.expires_at == expires
        assert api_key.is_valid()  # Should still be valid

    def test_create_key_without_user(self):
        """Test creating key without a user (system key)."""
        api_key, raw_key = APIKey.create_key(name="System Key")

        assert api_key.user is None
        assert api_key.name == "System Key"
        assert raw_key.startswith("cb_live_")

    def test_is_valid_active_key(self):
        """Test is_valid returns True for active key without expiration."""
        api_key = APIKeyFactory(is_active=True, expires_at=None)
        assert api_key.is_valid() is True

    def test_is_valid_inactive_key(self):
        """Test is_valid returns False for inactive key."""
        api_key = APIKeyFactory(is_active=False)
        assert api_key.is_valid() is False

    def test_is_valid_expired_key(self):
        """Test is_valid returns False for expired key."""
        expires = timezone.now() - timedelta(days=1)
        api_key = APIKeyFactory(is_active=True, expires_at=expires)
        assert api_key.is_valid() is False

    def test_is_valid_future_expiration(self):
        """Test is_valid returns True for key with future expiration."""
        expires = timezone.now() + timedelta(days=30)
        api_key = APIKeyFactory(is_active=True, expires_at=expires)
        assert api_key.is_valid() is True

    def test_last_used_at_nullable(self):
        """Test that last_used_at can be null."""
        api_key = APIKeyFactory()
        assert api_key.last_used_at is None

    def test_last_used_at_can_be_set(self):
        """Test that last_used_at can be updated."""
        api_key = APIKeyFactory()
        now = timezone.now()
        api_key.last_used_at = now
        api_key.save()
        api_key.refresh_from_db()
        assert api_key.last_used_at is not None

    def test_key_hash_unique_constraint(self):
        """Test that key_hash must be unique."""
        from django.db import IntegrityError

        hash_value = APIKey.hash_key("cb_live_test1234567890abcdef123456")
        APIKeyFactory(key_hash=hash_value, prefix="cb_live_test123")

        with pytest.raises(IntegrityError):
            APIKeyFactory(key_hash=hash_value, prefix="cb_live_test456")

    def test_ordering_by_created_desc(self):
        """Test that API keys are ordered by created date descending."""
        user = UserFactory()
        key1 = APIKeyFactory(user=user, name="First")
        key2 = APIKeyFactory(user=user, name="Second")

        keys = list(APIKey.objects.filter(user=user))
        assert keys[0] == key2  # More recent first
        assert keys[1] == key1

    def test_prefix_indexed(self):
        """Test that prefix field has db_index (helps with lookups)."""
        # This is a metadata test
        prefix_field = APIKey._meta.get_field("prefix")
        assert prefix_field.db_index is True  # type: ignore[attr-defined]

    def test_related_name_on_user(self):
        """Test that API keys can be accessed via user.api_keys."""
        user = UserFactory()
        key1 = APIKeyFactory(user=user, name="Key 1")
        key2 = APIKeyFactory(user=user, name="Key 2")

        user_keys = list(user.api_keys.all())
        assert len(user_keys) == 2
        assert key1 in user_keys
        assert key2 in user_keys

    def test_cascade_delete_on_user(self):
        """Test that API keys are deleted when user is deleted."""
        user = UserFactory()
        key = APIKeyFactory(user=user)
        key_id = key.id

        user.delete()

        assert not APIKey.objects.filter(id=key_id).exists()
