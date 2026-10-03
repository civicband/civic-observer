"""Tests for the rebackfill_failed_municipalities management command."""

import pytest

from municipalities.management.commands.rebackfill_failed_municipalities import (
    Command,
)
from tests.factories import MeetingDocumentFactory, MeetingPageFactory, MuniFactory


@pytest.mark.django_db
class TestGetMunicipalitiesWithPageCounts:
    def _rows_by_subdomain(self):
        rows = Command()._get_municipalities_with_page_counts()
        return {row.subdomain: row for row in rows}

    def test_counts_pages_per_municipality(self):
        muni_1 = MuniFactory(subdomain="count-city-1")
        muni_2 = MuniFactory(subdomain="count-city-2")
        doc_1 = MeetingDocumentFactory(municipality=muni_1)
        doc_2 = MeetingDocumentFactory(municipality=muni_2, document_type="minutes")
        MeetingPageFactory(document=doc_1, page_number=1)
        MeetingPageFactory(document=doc_1, page_number=2)
        MeetingPageFactory(document=doc_2, page_number=1)

        rows = self._rows_by_subdomain()

        assert rows["count-city-1"].page_count == 2
        assert rows["count-city-2"].page_count == 1

    def test_municipality_with_no_pages_counts_zero(self):
        MuniFactory(subdomain="empty-city")

        rows = self._rows_by_subdomain()

        assert rows["empty-city"].page_count == 0

    def test_returns_every_municipality(self):
        MuniFactory(subdomain="aaa-city")
        MuniFactory(subdomain="bbb-city")

        rows = self._rows_by_subdomain()

        assert "aaa-city" in rows
        assert "bbb-city" in rows
