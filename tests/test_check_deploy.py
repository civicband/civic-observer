"""Tests for the deploy readiness check."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command


def _mock_cursor(invalid_indexes):
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = list(invalid_indexes)
    return cursor


def _run_check(*args, invalid_indexes=(), pending=()):
    cursor = _mock_cursor(invalid_indexes)
    connection = MagicMock()
    connection.cursor.return_value = cursor

    executor = MagicMock()
    executor.return_value.loader.graph.leaf_nodes.return_value = []
    executor.return_value.migration_plan.return_value = [
        (SimpleNamespace(app_label=app_label, name=name), False)
        for app_label, name in pending
    ]

    with (
        patch("meetings.management.commands.check_deploy.connection", connection),
        patch("meetings.management.commands.check_deploy.MigrationExecutor", executor),
    ):
        call_command("check_deploy", *args)

    return cursor


def test_check_deploy_passes_with_no_pending_migrations_or_invalid_indexes():
    _run_check()


def test_check_deploy_fails_on_invalid_indexes():
    with pytest.raises(CommandError, match="public.bad_idx"):
        _run_check(invalid_indexes=[("public", "bad_idx")])


def test_check_deploy_fails_on_pending_migrations():
    with pytest.raises(CommandError, match="app.0001"):
        _run_check(pending=[("app", "0001")])


def test_check_deploy_can_skip_the_migration_check():
    _run_check("--skip-migrations", pending=[("app", "0001")])
