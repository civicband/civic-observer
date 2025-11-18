from typing import Any

from environs import env

from .development import *

# Override database configuration for tests
# Use localhost:5433 instead of docker service name for tests run outside docker
# Port 5433 is used to avoid conflict with any local PostgreSQL on port 5432
DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": env.dj_db_url(
        "DATABASE_URL", default="postgres://postgres@localhost:5433/postgres"
    ),
}

# Override RQ configuration for tests
# Use localhost instead of docker service name and run synchronously
REDIS_URL = "redis://localhost:6379/0"  # type: ignore[no-redef]
RQ_QUEUES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 360,
        "ASYNC": False,  # Run tasks synchronously in tests
    },
}
