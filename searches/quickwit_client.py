"""Quickwit client for managing search on meeting pages.

Quickwit uses an Elasticsearch-compatible REST API and stores index data
on S3-compatible object storage (Fastly in our case).
"""

import json
import logging
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_quickwit_url() -> str:
    """Return the base Quickwit REST API URL."""
    return getattr(settings, "QUICKWIT_URL", "http://quickwit:7280/api/v1")


def _get_quickwit_timeout() -> float:
    """Return the HTTP timeout for Quickwit requests (seconds)."""
    return float(getattr(settings, "QUICKWIT_TIMEOUT", 30))


def create_index(config_path: str | None = None) -> dict | None:
    """Create a Quickwit index using a YAML config file.

    This is typically run once during setup, not per-request.
    Uses 'quickwit index create' CLI via subprocess, since the REST API
    doesn't expose index creation.
    """
    import subprocess

    config = config_path or "quickwit/index-config.yaml"
    try:
        result = subprocess.run(
            ["quickwit", "index", "create", "--index-config", config],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info(f"Quickwit index created: {result.stdout.strip()}")
            return {"success": True, "output": result.stdout.strip()}
        else:
            if "already exists" in result.stderr.lower():
                logger.info("Quickwit index already exists")
                return {"success": True, "exists": True}
            logger.error(f"Quickwit index creation failed: {result.stderr}")
            return {"success": False, "error": result.stderr}
    except FileNotFoundError:
        logger.warning("quickwit CLI not installed. Use docker to create indexes.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Quickwit index creation timed out")
        return {"success": False, "error": "timeout"}


def delete_index() -> dict | None:
    """Delete the Quickwit index (development / reset only)."""
    import subprocess

    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")
    try:
        result = subprocess.run(
            ["quickwit", "index", "delete", "--index", index_id, "--yes"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info(f"Quickwit index deleted: {index_id}")
            return {"success": True}
        else:
            logger.error(f"Quickwit index deletion failed: {result.stderr}")
            return {"success": False, "error": result.stderr}
    except FileNotFoundError:
        logger.warning("quickwit CLI not installed")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Quickwit index deletion timed out")
        return {"success": False, "error": "timeout"}


def ingest_documents(
    documents: list[dict[str, Any]], input_format: str = "json"
) -> dict:
    """Ingest a batch of documents into the Quickwit index.

    Uses the ingest API: POST /api/v1/{index_id}/ingest
    """
    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")
    ingest_url = f"{_get_quickwit_url()}/{index_id}/ingest"

    if input_format == "json":
        body = "\n".join(json.dumps(doc, default=str) for doc in documents)
        headers = {"Content-Type": "application/x-ndjson"}
    else:
        body = ""
        headers = {}

    try:
        response = httpx.post(
            ingest_url,
            content=body.encode("utf-8"),
            headers=headers,
            timeout=_get_quickwit_timeout(),
        )
        response.raise_for_status()
        result = response.json() if response.content else {}
        logger.info(
            f"Ingested {len(documents)} documents into Quickwit index '{index_id}'"
        )
        return result
    except httpx.HTTPError as e:
        logger.error(f"Failed to ingest documents into Quickwit: {e}")
        return {"error": str(e)}


def execute_search(
    query_text: str,
    filters: dict | None = None,
    sort_by: str | None = None,
    sort_order: str = "desc",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Execute a search query against Quickwit.

    Uses the native Quickwit search API: POST /api/v1/search
    """
    url = f"{_get_quickwit_url()}/search"
    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")

    body: dict[str, Any] = {
        "index_id": index_id,
        "query": query_text,
        "max_hits": limit,
        "start_offset": offset,
    }

    if sort_by:
        body["sort_by_field"] = sort_by
        body["sort_order"] = sort_order

    if filters:
        body["filters"] = filters

    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=_get_quickwit_timeout(),
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Quickwit search failed: {e}")
        return {"num_hits": 0, "hits": []}


def execute_search_elasticsearch_compat(
    query_text: str,
    filters: dict | list | None = None,
    should: list | None = None,
    sort_by: list | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Execute a search using the Elasticsearch-compatible API.

    POST /api/v1/_elastic/{index_id}/_search

    Quickwit 0.8 ES-compatible API supports:
    - query_string for full-text search
    - bool queries with filter/must/should clauses
    """
    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")
    url = f"{_get_quickwit_url()}/_elastic/{index_id}/_search"

    es_body: dict[str, Any] = {
        "from": offset,
        "size": limit,
    }

    if sort_by:
        es_body["sort"] = sort_by
    else:
        es_body["sort"] = [{"meeting_date": "desc"}]

    # Build the bool query structure
    bool_query = {}

    if query_text:
        bool_query["must"] = [{"query_string": {"query": query_text}}]

    if filters:
        # filters can be a single dict or a list
        if isinstance(filters, list):
            bool_query["filter"] = filters
        else:
            bool_query["filter"] = [filters]

    if should:
        bool_query["should"] = should

    if query_text or filters or should:
        es_body["query"] = {"bool": bool_query}
    else:
        es_body["query"] = {"match_all": {}}

    try:
        response = httpx.post(
            url,
            json=es_body,
            headers={"Content-Type": "application/json"},
            timeout=_get_quickwit_timeout(),
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Quickwit ES-compatible search failed: {e}")
        return {"hits": {"total": {"value": 0}, "hits": []}}


def get_index_stats() -> dict:
    """Get statistics for the Quickwit index."""
    index_id = getattr(settings, "QUICKWIT_INDEX_ID", "meeting_pages")
    url = f"{_get_quickwit_url()}/{index_id}"
    try:
        response = httpx.get(url, timeout=_get_quickwit_timeout())
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to get Quickwit index stats: {e}")
        return {}


def health_check() -> bool:
    """Check if Quickwit is reachable and healthy."""
    # Quickwit 0.8 uses the index list endpoint as a health indicator
    url = f"{_get_quickwit_url()}/indexes"
    try:
        response = httpx.get(url, timeout=5.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False
