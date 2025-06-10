#!/usr/bin/env bash
set -eo pipefail

python manage.py tailwind build --noinput --skip-checks

python manage.py migrate --noinput --skip-checks

python manage.py collectstatic --noinput --skip-checks

exec "$@"
