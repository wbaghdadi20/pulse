"""Shared fixtures for the whole test suite.

pytest-django does the heavy lifting: it creates a separate `test_<db>` database
once per run, and wraps every test marked `django_db` in a transaction that is
rolled back afterwards, so each test starts from empty tables.
"""

import fcntl
import ipaddress
import os
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import redis
from django.core.cache import cache
from hypothesis import settings as hypothesis_settings
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import User
from monitors import clients, ssrf
from monitors.models import Monitor
from orgs import services as orgs
from orgs.models import Membership, Organization
from orgs.tenancy import current_tenant_id, use_public

# Property tests: a fast everyday profile, a thorough one for CI.
# Select with HYPOTHESIS_PROFILE=ci pytest
# No deadline in CI: a shared runner can stall one example past 200 ms. print_blob:
# CI keeps no example database, so the failure prints what reproduces it.
hypothesis_settings.register_profile(
    "ci", max_examples=2000, deadline=None, print_blob=True
)
hypothesis_settings.register_profile("dev", max_examples=500)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


REDIS_TURN = Path(tempfile.gettempdir()) / "pulse-tests-redis.lock"

MONITOR_PAYLOAD = {
    "name": "backend",
    "url": "http://target:8000/health",
    "timeout_s": 5,
    "interval_s": 30,
}
OWNER_EMAIL = "owner@example.com"

# Two organizations exist in every test database; tests run in the first by default
# when PULSE_TEST_IN_TENANT=1 (the spike's way of measuring the whole suite).
TENANT_SLUGS = ("acme", "globex")
ALL_IN_TENANT = os.environ.get("PULSE_TEST_IN_TENANT") == "1"
PUBLIC_PATHS = ("/t/", "/health", "/ready", "/signup", "/login")


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """The public tenant, the template and both organizations, once per database.
    Outside any test transaction: creating a schema is DDL that must commit."""
    with django_db_blocker.unblock():
        orgs.ensure_public_tenant()
        orgs.migrate_template()
        for slug in TENANT_SLUGS:
            if not Organization.objects.filter(slug=slug).exists():
                orgs.create_organization(slug.title(), slug)
        use_public()


def organization(slug: str) -> Organization:
    org = Organization.objects.get(slug=slug)
    assert isinstance(org, Organization)
    return org


@pytest.fixture
def acme(db) -> Iterator[Organization]:
    """The test runs inside acme's schema."""
    org = organization("acme")
    with org:
        yield org


@pytest.fixture
def globex(db) -> Organization:
    """The second organization, not entered: `with globex:` to work in it."""
    return organization("globex")


@pytest.fixture(autouse=ALL_IN_TENANT)
def in_tenant(request) -> None:
    """With PULSE_TEST_IN_TENANT=1, every test that touches the database runs in acme."""
    if "django_db" in request.keywords or "db" in request.fixturenames:
        request.getfixturevalue("acme")


class TenantClient(APIClient):
    """APIClient that sends tenant URLs to one organization: /monitors/ -> /t/acme/monitors/."""

    def __init__(self, slug: str = "acme", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.prefix = f"/t/{slug}"

    def request(self, **request: Any) -> Any:
        path = request["PATH_INFO"]
        if not path.startswith(PUBLIC_PATHS):
            request["PATH_INFO"] = self.prefix + path
        return super().request(**request)


def default_owner() -> User:
    """The account behind the `user` and `api` fixtures. No usable password.
    Inside a tenant it is a member there, so the tenant's endpoints let it in."""
    existing = User.objects.filter(email=OWNER_EMAIL).first()
    user = existing or User.objects.create_user(OWNER_EMAIL)
    tenant_id = current_tenant_id()
    if tenant_id is not None:
        Membership.objects.get_or_create(user=user, organization_id=tenant_id)
    return user


def create_monitor(owner: User | None = None, **fields: Any) -> Monitor:
    """A saved monitor: MONITOR_PAYLOAD with `fields` on top, owned by the default owner."""
    return Monitor.objects.create(
        owner=owner or default_owner(), **{**MONITOR_PAYLOAD, **fields}
    )


def client_for(user: User, slug: str | None = None) -> APIClient:
    """A client that sends this user's token, issued directly instead of through /login."""
    client = (
        TenantClient(slug) if slug else TenantClient() if ALL_IN_TENANT else APIClient()
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue(user.pk)}")
    return client


@pytest.fixture
def user(db) -> User:
    """The account that owns what the other fixtures create."""
    return default_owner()


@pytest.fixture
def api(user) -> APIClient:
    """DRF's test client, logged in as `user`. It talks to the Django app in-process."""
    return client_for(user)


@pytest.fixture
def anon() -> APIClient:
    """The same client with no token."""
    return TenantClient() if ALL_IN_TENANT else APIClient()


@pytest.fixture
def monitor(db) -> Monitor:
    """One saved monitor. Depending on `db` opts this fixture into the database."""
    return create_monitor()


@pytest.fixture(autouse=True)
def local_cache(settings) -> None:
    """Every test starts with an empty in-process cache instead of the dev Redis."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "KEY_FUNCTION": "django_tenants.cache.make_key",
        }
    }
    cache.clear()


class FakeDNS:
    """Stand-in for ssrf.resolve. Every name is on a public address unless a test
    gives it others: dns["postgres"] = ["172.18.0.3"]. An empty list means the name
    does not resolve. `lookups` lists the hosts that were asked for, in order."""

    PUBLIC = "93.184.216.34"

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.lookups: list[str] = []
        self.real = ssrf.resolve

    def __setitem__(self, host: str, addresses: list[str]) -> None:
        self.records[host] = addresses

    def resolve(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        if host in self.records:
            if not self.records[host]:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            return self.records[host]
        try:
            return [str(ipaddress.ip_address(host))]  # a literal is its own address
        except ValueError:
            return [self.PUBLIC]


@pytest.fixture(autouse=True)
def dns(monkeypatch: pytest.MonkeyPatch) -> FakeDNS:
    """No test looks a name up for real: results would depend on where it runs."""
    fake = FakeDNS()
    monkeypatch.setattr(ssrf, "resolve", fake.resolve)
    return fake


@pytest.fixture
def redis_db(monkeypatch: pytest.MonkeyPatch, settings) -> Iterator[redis.Redis]:
    """A real Redis, on database 15, emptied before and after each test.

    Locks are only as correct as Redis makes them, so they are tested against it.
    Database 15 keeps the tests away from the dev stack's queue (0) and locks (1).

    pytest-xdist workers are separate processes sharing that one database, and
    their monitors have the same ids, so the same lock keys. They take turns: the
    file lock is held from before the first flush until after the last.
    """
    url = urlsplit(settings.PULSE_REDIS_URL)._replace(path="/15").geturl()
    client = redis.Redis.from_url(url)
    with REDIS_TURN.open("w") as turn:
        fcntl.flock(turn, fcntl.LOCK_EX)
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
