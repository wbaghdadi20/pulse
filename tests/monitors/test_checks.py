"""run_check() against a fake HTTP transport: no network, no database.

httpx.MockTransport calls our handler instead of opening a socket. The handler
either returns a Response or raises the same exception httpx would raise.
"""

import socket
import time

import httpx
import pytest

from monitors import clients
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


def test_without_a_client_it_uses_the_shared_probe_client(monkeypatch):
    shared = fake_client(lambda request: httpx.Response(200))
    monkeypatch.setattr(clients, "probe_client", lambda: shared)

    assert run_check(make_monitor())["ok"] is True
    assert not shared.is_closed, "the shared client outlives the check"


def test_building_the_client_is_not_counted_as_latency(monkeypatch):
    def slow_to_build() -> httpx.Client:
        time.sleep(0.05)
        return fake_client(lambda request: httpx.Response(200))

    monkeypatch.setattr(clients, "probe_client", slow_to_build)

    assert run_check(make_monitor())["latency_ms"] < 50


def test_sends_the_monitors_timeout():
    seen = {}

    def handler(request):
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200)

    run_check(make_monitor(timeout_s=7), fake_client(handler))
    assert seen["timeout"] == {"connect": 7, "read": 7, "write": 7, "pool": 7}


def test_latency_is_measured_in_milliseconds():
    def slow(request):
        time.sleep(0.02)
        return httpx.Response(200)

    latency = run_check(make_monitor(), fake_client(slow))["latency_ms"]
    assert 20 <= latency < 1000


# --- JSON assertions (deep health checks) ------------------------------------------

DEEP = {
    "Database(alias='default')": "OK",
    "Redis(db=0, host='shop-redis', port=6379)": "OK",
}
HEALTHY = {**DEEP, "Storage(alias='default')": "unavailable: no bucket"}


def json_response(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_assertions_judge_the_body_not_the_status():
    # The deep check answers 500 because storage is down, but the keys we care about are OK.
    client = fake_client(lambda request: json_response(500, HEALTHY))
    result = run_check(make_monitor(assertions=DEEP), client)
    assert result["ok"] is True
    assert result["status_code"] == 500  # still recorded, for the operator
    assert result["error_type"] is None


@pytest.mark.parametrize(
    "body",
    [
        {
            **HEALTHY,
            "Redis(db=0, host='shop-redis', port=6379)": "unavailable: refused",
        },
        {"Database(alias='default')": "OK"},  # redis key missing
        ["not", "an", "object"],
    ],
)
def test_assertions_fail_when_a_key_is_wrong_or_missing(body):
    client = fake_client(lambda request: json_response(200, body))
    result = run_check(make_monitor(assertions=DEEP), client)
    assert result["ok"] is False
    assert result["error_type"] == "assertion_failed"


def test_assertions_fail_on_a_non_json_body():
    client = fake_client(lambda request: httpx.Response(200, text="<html>fine</html>"))
    result = run_check(make_monitor(assertions=DEEP), client)
    assert result["error_type"] == "assertion_failed"


def test_assertions_ask_for_json_and_plain_checks_do_not():
    seen = {}

    def handler(request):
        seen["accept"] = request.headers["accept"]
        return json_response(200, HEALTHY)

    run_check(make_monitor(assertions=DEEP), fake_client(handler))
    assert seen["accept"] == "application/json"
    run_check(make_monitor(), fake_client(handler))
    assert seen["accept"] == "*/*"  # httpx's default: the target decides the format


def test_network_errors_still_classify_with_assertions():
    client = fake_client(raising(lambda req: httpx.ReadTimeout("t", request=req)))
    assert run_check(make_monitor(assertions=DEEP), client)["error_type"] == "timeout"


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
