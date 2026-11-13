"""Can this process do its job right now? What /ready asks.

Why this is apart from /health, and what it cannot tell: docs/adr/0018-health-and-readiness.md
"""

import logging
from collections.abc import Callable

import redis
from django.conf import settings
from django.db import connection

log = logging.getLogger(__name__)


def database() -> None:
    """Raises unless Postgres answers a query. A new connection each time, limited
    by the connect_timeout in settings.DATABASES."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def redis_ping() -> None:
    """Raises unless the Redis behind the cache, the locks and the rate limit answers."""
    timeout = settings.PULSE_DEPENDENCY_TIMEOUT_S
    client = redis.Redis.from_url(
        settings.PULSE_REDIS_URL,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        client.ping()
    finally:
        client.close()


CHECKS: dict[str, Callable[[], None]] = {"database": database, "redis": redis_ping}


def answers() -> dict[str, bool]:
    """Each dependency by name: True if it answered."""
    return {name: _answers(name, check) for name, check in CHECKS.items()}


def _answers(name: str, check: Callable[[], None]) -> bool:
    try:
        check()
    except Exception:
        # Whatever went wrong, the answer is "not ready", never a 500. The reason
        # names hosts and users, so it goes to the log and not into the response.
        log.warning("readiness: %s failed", name, exc_info=True)
        return False
    return True
