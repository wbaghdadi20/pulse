import socket
import time
from typing import Any

import httpx

from .models import Monitor


def run_check(monitor: Monitor, client: httpx.Client | None = None) -> dict[str, Any]:
    """Call the monitor's URL once.

    A failing target is a normal result for a monitor, so this never raises:
    every outcome comes back as a dict.

    `client` is injectable so tests can plug in httpx.MockTransport instead of
    the network. Production passes nothing and gets a real client.
    """
    started = time.perf_counter()
    try:
        if client is None:
            with httpx.Client() as client:
                response = _send(client, monitor)
        else:
            response = _send(client, monitor)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": _elapsed_ms(started),
            "error_type": _classify(exc),
        }

    if monitor.assertions:
        # Deep health checks answer 500 when any component fails; the status code
        # would mark the service down for things we don't care about. Judge the
        # body by the asserted keys only.
        ok = _assertions_hold(response, monitor.assertions)
        error_type = None if ok else "assertion_failed"
    else:
        ok = response.status_code == monitor.expected_status
        error_type = None if ok else "wrong_status"
    return {
        "ok": ok,
        "status_code": response.status_code,
        "latency_ms": _elapsed_ms(started),
        "error_type": error_type,
    }


def _send(client: httpx.Client, monitor: Monitor) -> httpx.Response:
    # django-health-check and friends only return JSON when asked for it.
    headers = {"Accept": "application/json"} if monitor.assertions else None
    return client.request(
        monitor.method, monitor.url, timeout=monitor.timeout_s, headers=headers
    )


def _assertions_hold(response: httpx.Response, assertions: dict[str, str]) -> bool:
    """True if the body is a JSON object with every asserted key at its expected value."""
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    return all(body.get(key) == expected for key, expected in assertions.items())


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _classify(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    # httpx wraps the real OS error; walk the chain to find what actually failed.
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return "dns"
        if isinstance(cause, ConnectionRefusedError):
            return "connection_refused"
        cause = cause.__cause__ or cause.__context__
    return "error"  # anything else: TLS failure, protocol error, ...
