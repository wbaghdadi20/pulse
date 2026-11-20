"""Shared fixtures for the whole test suite.

pytest-django does the heavy lifting: it creates a separate `test_<db>` database
once per run, and wraps every test marked `django_db` in a transaction that is
rolled back afterwards, so each test starts from empty tables.

Every test database also holds the template schema and two organizations, acme and
globex, created once and committed. A test that uses the database runs inside acme's
schema, and its clients call /t/acme/... (docs/adr/0026).
"""

import fcntl
import ipaddress
import os
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import pytest
import redis
from django.apps import apps
from django.conf import settings as django_settings
from django.core.cache import cache, caches
from django.core.management.color import no_style
from django.db import connection
from django.test.utils import setup_databases, teardown_databases
from django.urls import Resolver404, resolve
from hypothesis import settings as hypothesis_settings
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import User
from monitors import clients, ssrf
from monitors.models import Monitor
from orgs import middleware
from orgs import services as orgs
from orgs.models import Membership, Organization
from orgs.tenancy import current_schema, in_organization

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
TENANT_SLUGS = ("acme", "globex")


def on_test_database() -> bool:
    return str(connection.settings_dict["NAME"]).startswith("test_")


@pytest.fixture(scope="session")
def django_db_setup(
    django_db_setup, django_db_blocker, django_db_keepdb, django_db_createdb
) -> Iterator[None]:
    """The template and both organizations, once per test database. Outside any
    test's transaction: they are committed, and kept by --reuse-db.

    pytest-django sets up a test database only for tests marked to use one. The
    fixtures that commit write without that mark, so the test database is set up
    here when nothing else did: never the development one.
    """
    created = None
    with django_db_blocker.unblock():
        if not on_test_database():
            created = setup_databases(
                verbosity=0,
                interactive=False,
                aliases=dict.fromkeys(["default"]),
                serialized_aliases=set(),
                keepdb=django_db_keepdb and not django_db_createdb,
            )
        assert on_test_database(), connection.settings_dict["NAME"]
        if not orgs.template_is_current():
            orgs.migrate_template()
        for slug in TENANT_SLUGS:
            if not Organization.objects.filter(slug=slug).exists():
                orgs.create_organization(slug.title(), slug)
            # Known to this process from the start, so no test's queries depend on
            # whether an earlier test already asked for the slug.
            middleware.tenant_for(slug)
    yield
    if created is not None and not django_db_keepdb:
        with django_db_blocker.unblock():
            teardown_databases(created, verbosity=0)


def organization(slug: str) -> Organization:
    org = Organization.objects.get(slug=slug)
    assert isinstance(org, Organization)
    return org


@pytest.fixture
def acme(db) -> Organization:
    """The organization every database test runs in (see in_tenant)."""
    return organization("acme")


@pytest.fixture
def globex(db) -> Organization:
    """The other organization, not entered: `with globex:` to work in its schema."""
    return organization("globex")


def uses_database(request: pytest.FixtureRequest) -> bool:
    names = request.fixturenames
    return (
        "django_db" in request.keywords or "db" in names or "transactional_db" in names
    )


@pytest.fixture(autouse=True)
def in_tenant(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every test that touches the database runs inside acme's schema."""
    if not uses_database(request):
        yield
        return
    with request.getfixturevalue("acme"):
        yield


@pytest.fixture(autouse=True)
def forget_organizations() -> Iterator[None]:
    """Organizations made in a test go with its transaction; so must the middleware's
    memory of their slugs. acme and globex stay."""
    yield
    middleware.forget_for_tests(keep=TENANT_SLUGS)


def use_public() -> None:
    """Leave the organization: what runs next sees public only."""
    connection.set_schema_to_public()  # type: ignore[attr-defined]


def empty_tenant_tables() -> None:
    """TRUNCATE every tenant table in the schema the connection is in."""
    tables = [
        model._meta.db_table
        for app in django_settings.TENANT_APPS
        for model in apps.get_app_config(app).get_models()
    ]
    with connection.cursor() as cursor:
        for statement in connection.ops.sql_flush(
            no_style(), tables, allow_cascade=True
        ):
            cursor.execute(statement)


@pytest.fixture
def committed(django_db_setup, django_db_blocker) -> Iterator[Organization]:
    """No test transaction, for a test that needs a second connection to see its rows.

    Runs in acme, and may write acme's tables only: they are emptied afterwards, and
    the test fails if it left anything behind in public.
    """
    shared = (User, Membership, Organization)
    with django_db_blocker.unblock():
        assert on_test_database(), connection.settings_dict["NAME"]
        before = [model.objects.count() for model in shared]
        org = organization("acme")
        with org:
            empty_tenant_tables()  # whatever an interrupted run left
            try:
                yield org
            finally:
                empty_tenant_tables()
        assert [model.objects.count() for model in shared] == before


@pytest.fixture
def template_being_migrated(monkeypatch) -> Iterator[None]:
    """Another connection holds the template's lock, as migrate_template does. A
    clone gives up after 0.2 s here, not the 2 s of production."""
    monkeypatch.setattr(orgs, "LOCK_TIMEOUT", "200ms")
    taken, done = threading.Event(), threading.Event()

    def hold() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [orgs.TEMPLATE_LOCK])
                taken.set()
                done.wait(10)
                cursor.execute("SELECT pg_advisory_unlock(%s)", [orgs.TEMPLATE_LOCK])
        finally:
            connection.close()

    thread = threading.Thread(target=hold)
    thread.start()
    assert taken.wait(5)
    yield
    done.set()
    thread.join(10)


class TenantClient(APIClient):
    """DRF's test client for one organization. What it adds: a path the public
    URLconf does not know, /monitors/ say, is sent as /t/<slug>/monitors/. Paths
    under /t/ and public ones (/signup, /health) go as they are."""

    def __init__(self, slug: str = "acme", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.prefix = f"/t/{slug}"

    def request(self, **request: Any) -> Any:
        path = request["PATH_INFO"]
        if not path.startswith("/t/") and not is_public(path):
            request["PATH_INFO"] = self.prefix + path
        return super().request(**request)


def is_public(path: str) -> bool:
    try:
        resolve(path, urlconf=django_settings.PUBLIC_SCHEMA_URLCONF)
    except Resolver404:
        return False
    return True


def default_owner() -> User:
    """The account behind the `user` and `api` fixtures. No usable password. What it
    adds: inside an organization, the account is also its owner (a membership), so
    that organization's endpoints let it in."""
    existing = User.objects.filter(email=OWNER_EMAIL).first()
    user = existing or User.objects.create_user(OWNER_EMAIL)
    if in_organization():
        membership_of(user)
    return user


def membership_of(user: User, org: Organization | None = None) -> Membership:
    """The user's membership in `org`, by default the one the connection is in,
    created as an owner if there is none: owner is the only role so far."""
    if org is None:
        assert in_organization(), "not inside an organization"
        org = Organization.objects.get(schema_name=current_schema())
    membership, _ = Membership.objects.get_or_create(
        user=user, organization=org, defaults={"role": Membership.Role.OWNER}
    )
    return membership


def create_monitor(
    assignee: Membership | None | Literal["default"] = "default", **fields: Any
) -> Monitor:
    """A saved monitor: MONITOR_PAYLOAD with `fields` on top. What it adds: unless the
    test says otherwise it is assigned to the default owner's membership, created
    if needed (None: to nobody). It skips the services' assignee check."""
    if assignee == "default":
        assignee = membership_of(default_owner())
    return Monitor.objects.create(assignee=assignee, **{**MONITOR_PAYLOAD, **fields})


def assignee_problems() -> list[str]:
    """TEN-2's rule, checked in every organization: each monitor's assignee is empty
    or an active membership of that organization. One line per monitor that breaks it."""
    problems = []
    for org in orgs.active_tenants():
        valid = set(
            Membership.objects.active()
            .filter(organization=org)
            .values_list("pk", flat=True)
        )
        with org:
            for pk, assignee in Monitor.objects.values_list("pk", "assignee_id"):
                if assignee is not None and assignee not in valid:
                    problems.append(
                        f"{org.slug}: monitor {pk} -> membership {assignee}"
                    )
    return problems


def client_for(user: User, slug: str = "acme") -> APIClient:
    """A client that sends this user's token, issued directly instead of through /login."""
    client = TenantClient(slug)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue(user.pk)}")
    return client


@pytest.fixture
def user(db) -> User:
    """The account that owns what the other fixtures create."""
    return default_owner()


@pytest.fixture
def membership(user) -> Membership:
    """The `user`'s membership in acme."""
    return membership_of(user)


@pytest.fixture
def api(user) -> APIClient:
    """DRF's test client, logged in as `user`. It talks to the Django app in-process."""
    return client_for(user)


@pytest.fixture
def anon() -> APIClient:
    """The same client with no token."""
    return TenantClient()


@pytest.fixture
def monitor(db) -> Monitor:
    """One saved monitor. Depending on `db` opts this fixture into the database."""
    return create_monitor()


@pytest.fixture(autouse=True)
def local_cache(settings) -> None:
    """Every test starts with an empty in-process cache instead of the dev Redis."""
    # The real aliases and their key functions; only where they are kept changes.
    settings.CACHES = {
        alias: {
            **config,
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": alias,
        }
        for alias, config in settings.CACHES.items()
    }
    cache.clear()
    caches["ratelimit"].clear()


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
