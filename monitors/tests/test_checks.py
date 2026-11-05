"""run_check() against a fake HTTP transport: no network, no database.

httpx.MockTransport calls our handler instead of opening a socket. The handler
either returns a Response or raises the same exception httpx would raise.
"""

import socket

import httpx
import pytest

from monitors.checks import run_check
from monitors.models import Monitor


def make_monitor(**overrides) -> Monitor:
    # Monitor(...) without .save(): a plain in-memory object, no database needed.
    fields = {
        "name": "t",
        "url": "http://target:8000/",
        "method": "GET",
        "expected_status": 200,
        "timeout_s": 5,
        "interval_s": 10,
    }
    return Monitor(**{**fields, **overrides})


def fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def raising(exc_factory):
    """A handler that raises whatever exc_factory(request) returns."""

    def handler(request):
        raise exc_factory(request)

    return handler


def test_matching_status_is_ok():
    client = fake_client(lambda request: httpx.Response(200))
    result = run_check(make_monitor(), client)
    assert result == {
        "ok": True,
        "status_code": 200,
        "latency_ms": result["latency_ms"],  # measured, not asserted
        "error_type": None,
    }
    assert result["latency_ms"] >= 0


def test_wrong_status():
    client = fake_client(lambda request: httpx.Response(500))
    result = run_check(make_monitor(), client)
    assert result["ok"] is False
    assert result["status_code"] == 500
    assert result["error_type"] == "wrong_status"


def test_expected_status_is_configurable():
    client = fake_client(lambda request: httpx.Response(204))
    assert run_check(make_monitor(expected_status=204), client)["ok"] is True


def test_timeout():
    client = fake_client(
        raising(lambda req: httpx.ReadTimeout("timed out", request=req))
    )
    result = run_check(make_monitor(), client)
    assert result["ok"] is False
    assert result["status_code"] is None
    assert result["error_type"] == "timeout"


def test_connection_refused():
    def handler(request):
        # httpx wraps the OS error; "from" sets __cause__ exactly like the real thing.
        raise httpx.ConnectError(
            "refused", request=request
        ) from ConnectionRefusedError()

    assert (
        run_check(make_monitor(), fake_client(handler))["error_type"]
        == "connection_refused"
    )


def test_dns_failure():
    def handler(request):
        raise httpx.ConnectError("unknown host", request=request) from socket.gaierror()

    assert run_check(make_monitor(), fake_client(handler))["error_type"] == "dns"


def test_other_network_error_is_classified_as_error():
    # e.g. a TLS failure: a ConnectError with no refused/dns cause underneath
    client = fake_client(raising(lambda req: httpx.ConnectError("tls", request=req)))
    assert run_check(make_monitor(), client)["error_type"] == "error"


def test_uses_the_monitors_method_and_url():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        return httpx.Response(200)

    run_check(make_monitor(method="HEAD", url="http://x:1/ht/"), fake_client(handler))
    assert seen == {"method": "HEAD", "url": "http://x:1/ht/"}


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda req: httpx.ReadTimeout("t", request=req),
        lambda req: httpx.ConnectError("c", request=req),
        lambda req: httpx.RemoteProtocolError("p", request=req),
    ],
)
def test_never_raises(exc_factory):
    client = fake_client(raising(exc_factory))
    result = run_check(make_monitor(), client)  # must not raise
    assert result["ok"] is False
