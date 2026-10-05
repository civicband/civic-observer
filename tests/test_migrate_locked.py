"""Tests for the advisory-lock-wrapped migrate command."""

from unittest.mock import MagicMock, patch

from django.core.management import call_command

from meetings.management.commands.migrate_locked import MIGRATION_ADVISORY_LOCK_KEY


def test_migrate_locked_serializes_and_runs_migrate():
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    connection = MagicMock()
    connection.cursor.return_value = cursor

    with (
        patch("meetings.management.commands.migrate_locked.connection", connection),
        patch(
            "meetings.management.commands.migrate_locked.call_command"
        ) as mock_migrate,
    ):
        call_command("migrate_locked", "--noinput")

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("pg_advisory_lock" in sql for sql in executed_sql)
    assert any("pg_advisory_unlock" in sql for sql in executed_sql)

    # The lock and unlock use the same key.
    lock_params = [
        call.args[1]
        for call in cursor.execute.call_args_list
        if "pg_advisory_lock" in call.args[0]
    ]
    unlock_params = [
        call.args[1]
        for call in cursor.execute.call_args_list
        if "pg_advisory_unlock" in call.args[0]
    ]
    assert lock_params == [[MIGRATION_ADVISORY_LOCK_KEY]]
    assert unlock_params == [[MIGRATION_ADVISORY_LOCK_KEY]]

    mock_migrate.assert_called_once()
    assert mock_migrate.call_args.args[0] == "migrate"
    assert mock_migrate.call_args.kwargs["interactive"] is False
