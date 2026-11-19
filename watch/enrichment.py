"""Thread enrichment.

Milestone 1 only needs the queue plumbing, so ``enrich_thread`` is a no-op
stub. Later milestones replace only its body; the RQ task signature stays the
same so queued jobs and the ingest path do not change.
"""


def enrich_thread(thread_id: str) -> dict[str, str | int]:
    """Return a placeholder result for a thread.

    Idempotent by design: it performs no writes and accepts any thread id,
    including ones that do not (yet) exist.
    """
    return {"status": "stubbed", "thread_id": str(thread_id)}
