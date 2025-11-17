from typing import Any

from .development import *

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
