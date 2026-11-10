"""Run migrations under a Postgres advisory lock.

Every web and worker container runs ``compose-entrypoint.sh``, so a blue-green
deploy can start several processes that all call ``migrate`` at once. Concurrent
migrations race on shared state. This command serializes them with a
session-level Postgres advisory lock.

The lock is acquired with ``pg_try_advisory_lock`` in a retry loop rather than
``pg_advisory_lock``. A blocking acquire leaves a transaction open while the
caller waits, and ``CREATE INDEX CONCURRENTLY`` waits for every transaction that
started before it. That combination deadlocks: the CIC waits on the waiter's
transaction, and the waiter waits on the CIC's advisory lock. Polling with a
short statement avoids holding a transaction while waiting.
"""

import time
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

# Fixed key shared by every container so they contend on a single lock.
MIGRATION_ADVISORY_LOCK_KEY = 65783120000001

# Keep these in sync with the command's defaults.
DEFAULT_LOCK_TIMEOUT_SECONDS = 3600.0
DEFAULT_LOCK_INTERVAL_SECONDS = 1.0


class Command(BaseCommand):
    help = "Run migrate while holding a Postgres advisory lock."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="Do not prompt for input.",
        )
        parser.add_argument(
            "--lock-timeout",
            type=float,
            default=DEFAULT_LOCK_TIMEOUT_SECONDS,
            help=(
                "Seconds to wait for another process to release the migration "
                f"lock (default: {DEFAULT_LOCK_TIMEOUT_SECONDS:g})."
            ),
        )
        parser.add_argument(
            "--lock-interval",
            type=float,
            default=DEFAULT_LOCK_INTERVAL_SECONDS,
            help=(
                "Seconds between lock attempts "
                f"(default: {DEFAULT_LOCK_INTERVAL_SECONDS:g})."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        timeout = max(options["lock_timeout"], 0.0)
        interval = max(options["lock_interval"], 0.01)
        deadline = time.monotonic() + timeout

        acquired = False
        while not acquired:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s)", [MIGRATION_ADVISORY_LOCK_KEY]
                )
                acquired = bool(cursor.fetchone()[0])

            if acquired:
                break
            if time.monotonic() >= deadline:
                raise CommandError(
                    f"Timed out after {timeout:g}s waiting for the migration lock"
                )
            time.sleep(interval)

        try:
            call_command(
                "migrate",
                interactive=options["interactive"],
                verbosity=options["verbosity"],
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s)", [MIGRATION_ADVISORY_LOCK_KEY]
                )
