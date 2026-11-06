"""Shared fixtures for the whole test suite.

pytest-django does the heavy lifting: it creates a separate `test_<db>` database
once per run, and wraps every test marked `django_db` in a transaction that is
rolled back afterwards, so each test starts from empty tables.
"""

import os

import pytest
from hypothesis import settings as hypothesis_settings
from rest_framework.test import APIClient

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
def fake_check(monkeypatch):
    """Replace the real HTTP call with a canned result.

    Returns a function to set what the next checks report, e.g. fake_check(ok=False).
    """
    state = {"result": _result(ok=True)}
    calls: list = []

    def set_result(**kwargs):
        state["result"] = _result(**kwargs)

    def fake_run_check(monitor, client=None):
        calls.append(monitor)
        return dict(state["result"])

    # services.py imported run_check by name, so patch it where it's looked up.
    monkeypatch.setattr("monitors.services.run_check", fake_run_check)
    set_result.calls = calls  # the monitors run_check was called with, in order
    return set_result


def _result(ok: bool = True, **overrides) -> dict:
    base = {
        "ok": ok,
        "status_code": 200 if ok else None,
        "latency_ms": 12,
        "error_type": None if ok else "timeout",
    }
    return {**base, **overrides}
