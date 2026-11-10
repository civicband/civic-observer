"""Helpers for migrations that need to run outside a transaction."""

from collections.abc import Iterable
from typing import Any


def _require_autocommit(connection: Any) -> None:
    if not connection.get_autocommit():
        raise RuntimeError(
            "Concurrent index operations require autocommit; "
            "set atomic = False on the migration"
        )


def create_index_concurrently(
    connection: Any,
    name: str,
    table: str,
    columns: Iterable[str],
) -> None:
    """Create a concurrent index and assert that Postgres marked it valid.

    ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction, so the
    migration must set ``atomic = False``. If a previous attempt was interrupted,
    an invalid index can be left behind. A plain ``CREATE INDEX CONCURRENTLY IF
    NOT EXISTS`` would skip that invalid index and let the migration be recorded
    as successful, so this helper always checks ``pg_index`` afterwards and
    raises instead of silently leaving the database slower.
    """
    _require_autocommit(connection)

    column_list = ", ".join(columns)
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE INDEX CONCURRENTLY {name} ON {table} ({column_list})")
        cursor.execute(
            "SELECT indisvalid, indisready FROM pg_index "
            "WHERE indexrelid = %s::regclass",
            [name],
        )
        row = cursor.fetchone()

    if row is None or not row[0] or not row[1]:
        raise RuntimeError(
            f"Concurrent index {name} is not valid after creation; "
            "drop it and retry the migration"
        )


def drop_index_concurrently(connection: Any, name: str) -> None:
    """Drop an index concurrently; used as the reverse of a CIC migration."""
    _require_autocommit(connection)

    with connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
