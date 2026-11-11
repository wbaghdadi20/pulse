"""Pulse's long-lived network clients: one of each per process, built on first use.

Why shared, and why lazily: docs/adr/0001-shared-http-clients.md
"""

from functools import cache

import httpx
import redis
from django.conf import settings

WEBHOOK_TIMEOUT_S = 5


@cache
def probe_client() -> httpx.Client:
    """For checks. Keep-alive is off: every check opens a new connection.

    No timeout here: each check passes its monitor's timeout_s.
    """
    return httpx.Client(limits=httpx.Limits(max_keepalive_connections=0))


@cache
def webhook_client() -> httpx.Client:
    """For alerts. Connections to the receiver are reused."""
    return httpx.Client(timeout=WEBHOOK_TIMEOUT_S)


@cache
def redis_client() -> redis.Redis:
    """For locks. The cache goes through Django's cache framework instead."""
    return redis.Redis.from_url(settings.PULSE_REDIS_URL)
