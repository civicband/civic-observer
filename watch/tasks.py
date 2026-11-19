"""RQ task entry points for the watch app.

Workers import task functions from this module, which keeps the job surface
separate from ``watch.ingest``. Importing it must stay side-effect free so an
enqueue failure can never affect an ingest transaction.
"""

from watch.enrichment import enrich_thread

__all__ = ["enrich_thread"]
