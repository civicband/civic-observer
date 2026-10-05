#!/usr/bin/env bash
set -eo pipefail

# Serialize migrations across the container fleet with a Postgres advisory
# lock, so a blue-green deploy starting several web/worker containers at once
# cannot run concurrent migrations.
python manage.py migrate_locked --noinput

exec "$@"
