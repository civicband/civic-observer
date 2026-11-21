"""Thread enrichment.

Enrichment classifies the thread's subreddit when that row is not operator-owned,
rolls extracted places up to the thread, and refreshes the thread's cached
meeting hits. The RQ task signature stays the same so queued jobs and the
ingest path do not change.
"""

from django.core.exceptions import ValidationError

from watch.classify import apply_classification, classify_subreddit
from watch.extraction import extract_thread_places
from watch.lookup import refresh_thread_hits
from watch.models import Subreddit, Thread

#: Sources that represent a trusted label or an operator decision. Enrichment
#: must not overwrite them with a fresh gazetteer suggestion.
_TRUSTED_CLASSIFICATION_SOURCES = frozenset(
    {
        Subreddit.Source.MANUAL,
        Subreddit.Source.WIKIDATA,
        Subreddit.Source.TEXT_EVIDENCE,
        Subreddit.Source.LLM,
    }
)


def ensure_subreddit_classification(thread: Thread) -> bool:
    """Classify the thread's subreddit unless a trusted label already exists.

    Returns whether the subreddit row changed. Rows from a trusted source,
    rows the operator has reviewed, and rows already classified keep their
    current labels; in particular, a skipped row is never stamped with
    ``classified_at``.
    """
    subreddit = thread.subreddit
    if subreddit is None:
        return False
    if subreddit.source in _TRUSTED_CLASSIFICATION_SOURCES:
        return False
    if subreddit.classified_at is not None or subreddit.reviewed_at is not None:
        return False
    return apply_classification(subreddit, classify_subreddit(subreddit.name))


def enrich_thread(thread_id: str) -> dict[str, str | int]:
    """Classify, extract places, and refresh meeting hits for one thread.

    Idempotent by design: a missing or malformed thread id is reported as
    ``not_found`` rather than raising, and repeated runs replace the cached
    place and hit rows in place.
    """
    try:
        thread = Thread.objects.filter(pk=thread_id).first()
    except (ValidationError, ValueError):
        thread = None
    if thread is None:
        return {"status": "not_found", "thread_id": thread_id}

    ensure_subreddit_classification(thread)
    rolled = extract_thread_places(thread)
    return {
        "status": "ok",
        "thread_id": str(thread.id),
        "subjects": rolled["subjects"],
        "mentioned": rolled["mentioned"],
        "hits": refresh_thread_hits(thread),
    }
