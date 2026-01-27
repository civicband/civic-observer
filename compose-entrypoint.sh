#!/usr/bin/env bash
set -eo pipefail

python manage.py migrate --noinput --skip-checks

exec "$@"
