"""Compatibility shim exposing watch tasks.

The ingest path enqueues ``watch.enrichment.enrich_thread`` directly, so this
module simply re-exports it for callers that prefer to import task functions
from ``watch.tasks``. Importing it stays side-effect free so enqueue problems
can never affect an ingest transaction.
"""

from watch.enrichment import enrich_thread

__all__ = ["enrich_thread"]
