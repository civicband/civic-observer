"""Fail fast when the database is not ready to serve this release.

A migration can finish and be recorded while leaving an invalid index behind,
for example when ``CREATE INDEX CONCURRENTLY`` is interrupted. Postgres ignores
invalid indexes, so the application keeps serving but silently loses the
performance improvement. This command is meant to run after migrations in CI
and in the deploy entrypoint so that state cannot pass unnoticed.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

INVALID_INDEXES_SQL = """
SELECT n.nspname, c.relname
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE (NOT i.indisvalid OR NOT i.indisready)
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname, c.relname
"""


class Command(BaseCommand):
    help = "Fail if migrations are unapplied or an index is invalid."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--skip-migrations",
            action="store_true",
            help="Only check indexes; useful when migrations run separately.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        problems: list[str] = []

        if not options["skip_migrations"]:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if plan:
                pending = ", ".join(
                    f"{migration.app_label}.{migration.name}"
                    for migration, _backwards in plan
                )
                problems.append(f"Unapplied migrations: {pending}")

        with connection.cursor() as cursor:
            cursor.execute(INVALID_INDEXES_SQL)
            invalid_indexes = cursor.fetchall()

        if invalid_indexes:
            names = ", ".join(f"{schema}.{name}" for schema, name in invalid_indexes)
            problems.append(
                "Invalid indexes (a concurrent build likely failed): " + names
            )

        if problems:
            raise CommandError("\n".join(problems))

        self.stdout.write(self.style.SUCCESS("Deploy checks passed"))
