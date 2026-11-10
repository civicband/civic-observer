"""Tests for the advisory-lock-wrapped migrate command."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

from meetings.management.commands.migrate_locked import MIGRATION_ADVISORY_LOCK_KEY


def _mock_connection(acquire_results):
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.side_effect = [(result,) for result in acquire_results]

    connection = MagicMock()
    connection.cursor.return_value = cursor
    return connection, cursor


def _patch_connection(connection):
    return patch("meetings.management.commands.migrate_locked.connection", connection)


def test_migrate_locked_tries_the_lock_without_blocking():
    connection, cursor = _mock_connection([True])

    with (
        _patch_connection(connection),
        patch("meetings.management.commands.migrate_locked.call_command") as migrate,
        patch("meetings.management.commands.migrate_locked.time.sleep") as sleep,
    ):
        call_command("migrate_locked", "--noinput")

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("pg_try_advisory_lock" in sql for sql in executed_sql)
    assert not any("SELECT pg_advisory_lock(" in sql for sql in executed_sql)
    assert any("pg_advisory_unlock" in sql for sql in executed_sql)
    sleep.assert_not_called()
    migrate.assert_called_once()
    assert migrate.call_args.args[0] == "migrate"
    assert migrate.call_args.kwargs["interactive"] is False


def test_migrate_locked_retries_until_the_lock_is_acquired():
    connection, cursor = _mock_connection([False, False, True])

    with (
        _patch_connection(connection),
        patch("meetings.management.commands.migrate_locked.call_command") as migrate,
        patch("meetings.management.commands.migrate_locked.time.sleep") as sleep,
    ):
        call_command("migrate_locked", "--noinput", "--lock-interval", "0.01")

    assert sleep.call_count == 2
    sleep.assert_called_with(0.01)
    migrate.assert_called_once()

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum("pg_try_advisory_lock" in sql for sql in executed_sql) == 3


def test_migrate_locked_times_out_without_running_migrate():
    connection, cursor = _mock_connection([False])

    with (
        _patch_connection(connection),
        patch("meetings.management.commands.migrate_locked.call_command") as migrate,
        patch("meetings.management.commands.migrate_locked.time.sleep") as sleep,
    ):
        with pytest.raises(CommandError, match="Timed out"):
            call_command("migrate_locked", "--noinput", "--lock-timeout", "0")

    sleep.assert_not_called()
    migrate.assert_not_called()
    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any("pg_advisory_unlock" in sql for sql in executed_sql)


def test_migrate_locked_unlocks_when_migrate_fails():
    connection, cursor = _mock_connection([True])

    with (
        _patch_connection(connection),
        patch(
            "meetings.management.commands.migrate_locked.call_command",
            side_effect=RuntimeError("boom"),
        ),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            call_command("migrate_locked", "--noinput")

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("pg_try_advisory_lock" in sql for sql in executed_sql)
    assert any("pg_advisory_unlock" in sql for sql in executed_sql)

    lock_params = [
        call.args[1]
        for call in cursor.execute.call_args_list
        if "pg_try_advisory_lock" in call.args[0]
    ]
    unlock_params = [
        call.args[1]
        for call in cursor.execute.call_args_list
        if "pg_advisory_unlock" in call.args[0]
    ]
    assert lock_params == [[MIGRATION_ADVISORY_LOCK_KEY]]
    assert unlock_params == [[MIGRATION_ADVISORY_LOCK_KEY]]
