import socket
import time

import httpx


def run_check(monitor: dict) -> dict:
    """Call the monitor's URL once.

    A failing target is a normal result for a monitor, so this never raises:
    every outcome comes back as a dict.
    """
    started = time.perf_counter()
    try:
        response = httpx.request(
            monitor["method"], monitor["url"], timeout=monitor["timeout_s"]
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": _elapsed_ms(started),
            "error_type": _classify(exc),
        }

    ok = response.status_code == monitor["expected_status"]
    return {
        "ok": ok,
        "status_code": response.status_code,
        "latency_ms": _elapsed_ms(started),
        "error_type": None if ok else "wrong_status",
    }


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _classify(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    # httpx wraps the real OS error; walk the chain to find what actually failed.
    cause = exc
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return "dns"
        if isinstance(cause, ConnectionRefusedError):
            return "connection_refused"
        cause = cause.__cause__ or cause.__context__
    return "error"  # anything else: TLS failure, protocol error, ...
