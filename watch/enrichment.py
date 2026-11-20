"""Thread enrichment.

Enrichment currently refreshes the thread's cached meeting hits.  Later
milestones add classification and extraction here; the RQ task signature stays
the same so queued jobs and the ingest path do not change.
"""

from django.core.exceptions import ValidationError

from watch.lookup import refresh_thread_hits
from watch.models import Thread


def enrich_thread(thread_id: str) -> dict[str, str | int]:
    """Refresh meeting hits for one thread and report what happened.

    Idempotent by design: a missing or malformed thread id is reported as
    ``not_found`` rather than raising, and repeated runs replace the cached
    hits in place.
    """
    try:
        thread = Thread.objects.filter(pk=thread_id).first()
    except (ValidationError, ValueError):
        thread = None
    if thread is None:
        return {"status": "not_found", "thread_id": thread_id}

    return {
        "status": "ok",
        "thread_id": str(thread.id),
        "hits": refresh_thread_hits(thread),
    }
