import json
import socket
import time
from typing import TypedDict

import httpx

from . import clients, ssrf
from .models import Monitor


class CheckResult(TypedDict):
    """One check's outcome: the columns of a `checks` row, and the "check now" response."""

    ok: bool
    status_code: int | None
    latency_ms: int
    error_type: str | None


def probe(monitor: Monitor, client: httpx.Client | None = None) -> CheckResult:
    """Call the monitor's URL once.

    A failing target is a normal result for a monitor, so this never raises:
    every outcome comes back as a CheckResult.

    `client` defaults to this process's probe client (see clients.py). Tests pass
    one built on httpx.MockTransport instead of the network.
    """
    if client is None:
        client = clients.probe_client()
    started = time.perf_counter()
    try:
        # On every check, not only when the monitor was created: DNS can change.
        address = ssrf.safe_address(monitor.url)
        status_code, body = _fetch(
            client, monitor, address, started + monitor.timeout_s
        )
    except ssrf.BlockedAddress:
        return _failed(started, "blocked")
    except socket.gaierror:
        return _failed(started, "dns")
    except httpx.InvalidURL:
        return _failed(started, "error")
    except httpx.HTTPError as exc:
        return _failed(started, _classify(exc))

    if monitor.assertions:
        # Deep health checks answer 500 when any component fails; the status code
        # would mark the service down for things we don't care about. Judge the
        # body by the asserted keys only.
        ok = _assertions_hold(body, monitor.assertions)
        error_type = None if ok else "assertion_failed"
    else:
        ok = status_code == monitor.expected_status
        error_type = None if ok else "wrong_status"
    return {
        "ok": ok,
        "status_code": status_code,
        "latency_ms": _elapsed_ms(started),
        "error_type": error_type,
    }


def _fetch(
    client: httpx.Client, monitor: Monitor, address: str, deadline: float
) -> tuple[int, bytes]:
    """Send the request to `address` and read the whole answer: (status code, body).

    The connection goes to the address that was judged safe, not to the name: httpx
    would look the name up again, and that answer has not been judged. The name
    still travels in the Host header and, for https, in the TLS handshake, so
    virtual hosts and certificates work as if the name had been used.

    httpx's timeout limits each wait for the next bytes, not the request as a whole,
    so a target that keeps sending a little never trips it. The deadline does:
    past it, the check stops reading and counts as a timeout.
    """
    target = httpx.URL(monitor.url)
    headers = {"Host": target.netloc.decode("ascii")}
    if monitor.assertions:
        # django-health-check and friends only return JSON when asked for it.
        headers["Accept"] = "application/json"
    with client.stream(
        monitor.method,
        target.copy_with(host=address),
        timeout=monitor.timeout_s,
        headers=headers,
        extensions={"sni_hostname": target.host},
    ) as response:
        chunks = []
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            if time.perf_counter() > deadline:
                raise httpx.ReadTimeout(
                    "the response took longer than timeout_s in total",
                    request=response.request,
                )
        return response.status_code, b"".join(chunks)


def _assertions_hold(body: bytes, assertions: dict[str, str]) -> bool:
    """True if the body is a JSON object with every asserted key at its expected value."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    return all(parsed.get(key) == expected for key, expected in assertions.items())


def _failed(started: float, error_type: str) -> CheckResult:
    return {
        "ok": False,
        "status_code": None,
        "latency_ms": _elapsed_ms(started),
        "error_type": error_type,
    }


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _classify(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    # httpx wraps the real OS error; walk the chain to find what actually failed.
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ConnectionRefusedError):
            return "connection_refused"
        cause = cause.__cause__ or cause.__context__
    return "error"  # anything else: TLS failure, protocol error, ...
