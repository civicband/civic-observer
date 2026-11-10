#!/usr/bin/env bash
set -eo pipefail

# Serialize migrations across the container fleet with a Postgres advisory
# lock, so a blue-green deploy starting several web/worker containers at once
# cannot run concurrent migrations. The command polls with a non-blocking lock
# so waiters do not hold a transaction open while CREATE INDEX CONCURRENTLY runs.
python manage.py migrate_locked --noinput

# Refuse to serve if a migration left an invalid index or is still unapplied.
python manage.py check_deploy

exec "$@"
