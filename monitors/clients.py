"""Pulse's two outbound HTTP clients: one of each per process, built on first use.

An httpx.Client owns a connection pool and the TLS configuration, and is safe to
share between threads, so it is meant to live as long as the process does. Building
one per call throws both away every time.

"On first use" rather than at import: gunicorn and Celery fork their workers after
the code is imported, and a client built before the fork would be shared with the
parent. Built lazily, each worker process ends up with its own.
"""

from functools import cache

import httpx

WEBHOOK_TIMEOUT_S = 5


@cache
def probe_client() -> httpx.Client:
    """For checks: every request opens a new connection, on purpose.

    A check has to answer "can someone reach this service right now?", which
    includes resolving the name, connecting and the TLS handshake. A connection
    kept alive from the previous check would skip all three: latency would look
    better than a visitor's, and a target whose DNS record is gone would stay "up"
    for as long as the old connection lasted. Prometheus' blackbox exporter
    disables keep-alive for the same reason.

    No timeout here: each check passes its monitor's timeout_s.
    """
    return httpx.Client(limits=httpx.Limits(max_keepalive_connections=0))


@cache
def webhook_client() -> httpx.Client:
    """For alerts: one receiver, called again and again, so connections are reused."""
    return httpx.Client(timeout=WEBHOOK_TIMEOUT_S)
