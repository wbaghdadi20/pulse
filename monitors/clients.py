"""Pulse's two outbound HTTP clients: one of each per process, built on first use.

Why two, and why lazily: docs/adr/0001-shared-http-clients.md
"""

from functools import cache

import httpx

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
