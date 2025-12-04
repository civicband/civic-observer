"""
Service module for fetching meeting pages from civic.band API.
"""

import logging
from datetime import date

import httpx
from django.conf import settings
from django.db import transaction

from meetings.models import MeetingDocument, MeetingPage
from municipalities.models import Muni

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Exception raised when fetching from civic.band fails."""

    pass


def fetch_single_page(page_id: str, subdomain: str, table: str) -> MeetingPage | None:
    """
    Fetch a single page from civic.band API and store it locally.

    Args:
        page_id: The civic.band page ID
        subdomain: Municipality subdomain
        table: "agendas" or "minutes"

    Returns:
        MeetingPage instance if found and created, None otherwise

    Raises:
        FetchError: If the fetch operation fails
    """
    # Get or create the municipality
    try:
        muni = Muni.objects.get(subdomain=subdomain)
    except Muni.DoesNotExist as e:
        logger.warning(f"Municipality not found: {subdomain}")
        raise FetchError(
            f"Municipality '{subdomain}' not found in civic.observer"
        ) from e

    # Build API URL to fetch the specific page
    # The civic.band API supports filtering by ID
    base_url = f"https://{subdomain}.civic.band/meetings/{table}.json"
    url = f"{base_url}?id={page_id}"

    # Build headers with service secret for authentication
    headers = {}
    service_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", "")
    if service_secret:
        headers["X-Service-Secret"] = service_secret

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            logger.debug(f"Fetching {url}")
            response = client.get(url)
            response.raise_for_status()

            data = response.json()
            rows = data.get("rows", [])

            if not rows:
                logger.info(f"Page not found in civic.band: {page_id}")
                return None

            # Process the first matching row
            row = rows[0]
            return _create_page_from_row(muni, row, table)

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching page {page_id}: {e}", exc_info=True)
        raise FetchError(f"Failed to fetch from civic.band: {e}") from e


def _create_page_from_row(muni: Muni, row: dict, table: str) -> MeetingPage | None:
    """
    Create a MeetingDocument and MeetingPage from a civic.band API row.

    Args:
        muni: Municipality instance
        row: Row dictionary from the API
        table: "agendas" or "minutes"

    Returns:
        MeetingPage instance if created successfully
    """
    page_id = row.get("id")
    meeting_name = row.get("meeting", "")
    date_str = row.get("date", "")
    page_number = row.get("page", 0)
    text = row.get("text", "")
    page_image = row.get("page_image", "")

    if not all([page_id, meeting_name, date_str]):
        logger.warning(f"Skipping row with missing data: {row}")
        return None

    # Parse date
    try:
        meeting_date = date.fromisoformat(date_str)
    except ValueError:
        logger.warning(f"Invalid date format: {date_str}")
        return None

    # Convert table to document_type
    document_type = "agenda" if table == "agendas" else "minutes"

    try:
        with transaction.atomic():
            # Create or get the document
            document, _ = MeetingDocument.objects.get_or_create(
                municipality=muni,
                meeting_name=meeting_name,
                meeting_date=meeting_date,
                document_type=document_type,
            )

            # Create or update the page
            page, _ = MeetingPage.objects.update_or_create(
                id=page_id,
                defaults={
                    "document": document,
                    "page_number": page_number,
                    "text": text,
                    "page_image": page_image,
                },
            )

            return page

    except Exception as e:
        logger.error(f"Error creating page {page_id}: {e}", exc_info=True)
        return None
