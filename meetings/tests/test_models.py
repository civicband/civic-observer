"""Tests for meetings models."""

from datetime import datetime

import pytest

from meetings.models import BackfillProgress
from municipalities.models import Muni


@pytest.mark.django_db
class TestBackfillProgress:
    def test_create_backfill_progress(self):
        """Test creating a BackfillProgress record."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="pending",
        )

        assert progress.municipality == muni
        assert progress.document_type == "agenda"
        assert progress.mode == "full"
        assert progress.status == "pending"
        assert progress.next_cursor is None
        assert progress.force_full_backfill is False
        assert isinstance(progress.started_at, datetime)
        assert isinstance(progress.updated_at, datetime)

    def test_unique_together_constraint(self):
        """Test that municipality + document_type must be unique."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="pending",
        )

        # Trying to create duplicate should raise IntegrityError
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            BackfillProgress.objects.create(
                municipality=muni,
                document_type="agenda",
                mode="incremental",
                status="pending",
            )
