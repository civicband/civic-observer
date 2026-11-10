"""Tests for migration helpers."""

from unittest.mock import MagicMock

import pytest

from meetings.migration_utils import (
    create_index_concurrently,
    drop_index_concurrently,
)


def _mock_connection(*, autocommit: bool = True, validity=(True, True)):
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.return_value = validity

    connection = MagicMock()
    connection.get_autocommit.return_value = autocommit
    connection.cursor.return_value = cursor
    return connection, cursor


def test_create_index_concurrently_creates_and_verifies_the_index():
    connection, cursor = _mock_connection()

    create_index_concurrently(
        connection,
        "meetings_meetingpage_modified_idx",
        "meetings_meetingpage",
        ["modified"],
    )

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert (
        "CREATE INDEX CONCURRENTLY meetings_meetingpage_modified_idx "
        "ON meetings_meetingpage (modified)" in executed_sql
    )
    assert any("indisvalid" in sql for sql in executed_sql)


def test_create_index_concurrently_rejects_a_leftover_invalid_index():
    connection, _ = _mock_connection(validity=(False, True))

    with pytest.raises(RuntimeError, match="not valid"):
        create_index_concurrently(
            connection,
            "meetings_meetingpage_modified_idx",
            "meetings_meetingpage",
            ["modified"],
        )


def test_create_index_concurrently_requires_autocommit():
    connection, cursor = _mock_connection(autocommit=False)

    with pytest.raises(RuntimeError, match="autocommit"):
        create_index_concurrently(
            connection,
            "meetings_meetingpage_modified_idx",
            "meetings_meetingpage",
            ["modified"],
        )

    cursor.execute.assert_not_called()


def test_drop_index_concurrently_executes_the_drop():
    connection, cursor = _mock_connection()

    drop_index_concurrently(connection, "meetings_meetingpage_modified_idx")

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert (
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "meetings_meetingpage_modified_idx" in executed_sql
    )


def test_drop_index_concurrently_requires_autocommit():
    connection, cursor = _mock_connection(autocommit=False)

    with pytest.raises(RuntimeError, match="autocommit"):
        drop_index_concurrently(connection, "meetings_meetingpage_modified_idx")

    cursor.execute.assert_not_called()
