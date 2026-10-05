"""Run migrations under a Postgres advisory lock.

Every web and worker container runs ``compose-entrypoint.sh``, so a blue-green
deploy can start several processes that all call ``migrate`` at once. Concurrent
migrations race on shared state. This command serializes them with a
session-level Postgres advisory lock, held for the duration of ``migrate`` and
released automatically when the connection closes (and explicitly in a finally
block).
"""

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser
from django.db import connection

# Fixed key shared by every container so they contend on a single lock.
MIGRATION_ADVISORY_LOCK_KEY = 65783120000001


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

    def handle(self, *args: Any, **options: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_ADVISORY_LOCK_KEY])
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
