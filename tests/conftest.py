"""Shared fixtures for the whole test suite.

pytest-django does the heavy lifting: it creates a separate `test_<db>` database
once per run, and wraps every test marked `django_db` in a transaction that is
rolled back afterwards, so each test starts from empty tables.
"""

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import redis
from hypothesis import settings as hypothesis_settings
from rest_framework.test import APIClient

from monitors import clients
from monitors.models import Monitor

# Property tests: a fast everyday profile, a thorough one for CI.
# Select with HYPOTHESIS_PROFILE=ci pytest
hypothesis_settings.register_profile("ci", max_examples=2000)
hypothesis_settings.register_profile("dev", max_examples=500)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


MONITOR_PAYLOAD = {
    "name": "backend",
    "url": "http://target:8000/health",
    "timeout_s": 5,
    "interval_s": 30,
}


@pytest.fixture
def api() -> APIClient:
    """DRF's test client: like FastAPI's TestClient, but talks to the Django app in-process."""
    return APIClient()


@pytest.fixture
def monitor(db) -> Monitor:
    """One saved monitor. Depending on `db` opts this fixture into the database."""
    return Monitor.objects.create(**MONITOR_PAYLOAD)


@pytest.fixture
def redis_db(monkeypatch: pytest.MonkeyPatch, settings) -> Iterator[redis.Redis]:
    """A real Redis, on database 15, emptied before and after each test.

    Locks are only as correct as Redis makes them, so they are tested against it.
    Database 15 keeps the tests away from the dev stack's queue (0) and locks (1).
    """
    url = urlsplit(settings.PULSE_REDIS_URL)._replace(path="/15").geturl()
    client = redis.Redis.from_url(url)
    client.flushdb()
    monkeypatch.setattr(clients, "redis_client", lambda: client)
    yield client
    client.flushdb()
    client.close()


class FakeCheck:
    """Stand-in for monitors.checks.probe that returns a canned result.

    Call it to set what the next checks report, e.g. fake_check(ok=False).
    `calls` lists the monitors probe was called with, in order.
    """

    def __init__(self) -> None:
        self.result = _result(ok=True)
        self.calls: list[Monitor] = []

    def __call__(self, **kwargs: Any) -> None:
        self.result = _result(**kwargs)

    def probe(
        self, monitor: Monitor, client: httpx.Client | None = None
    ) -> dict[str, Any]:
        self.calls.append(monitor)
        return dict(self.result)


@pytest.fixture
def fake_check(monkeypatch: pytest.MonkeyPatch) -> FakeCheck:
    """Replace the real HTTP call with a canned result."""
    fake = FakeCheck()
    # services.py imported probe by name, so patch it where it's looked up.
    monkeypatch.setattr("monitors.services.probe", fake.probe)
    return fake


def _result(ok: bool = True, **overrides: Any) -> dict[str, Any]:
    base = {
        "ok": ok,
        "status_code": 200 if ok else None,
        "latency_ms": 12,
        "error_type": None if ok else "timeout",
    }
    return {**base, **overrides}
