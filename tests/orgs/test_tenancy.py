"""Spike: two organizations side by side, each in its own schema (docs/adr/0026)."""

from typing import Any

import pytest
from django.core.cache import cache
from django.db import ProgrammingError
from django.test.utils import override_settings

from accounts.models import User
from monitors import locks, tasks
from monitors.models import Check, Monitor
from orgs.models import Membership, Organization
from orgs.tenancy import current_schema, use_public
from tests.conftest import MONITOR_PAYLOAD, TenantClient, client_for, create_monitor

pytestmark = pytest.mark.django_db


def member_of(org: Organization, email: str) -> User:
    user = User.objects.create_user(email)
    Membership.objects.create(user=user, organization=org)
    return user


@pytest.fixture
def alice(acme) -> User:
    return member_of(acme, "alice@example.com")


@pytest.fixture
def gina(globex) -> User:
    return member_of(globex, "gina@example.com")


def test_each_organization_lists_and_creates_its_own_monitors(alice, gina):
    acme_api, globex_api = client_for(alice, "acme"), client_for(gina, "globex")

    made = acme_api.post(
        "/monitors/", {**MONITOR_PAYLOAD, "name": "web"}, format="json"
    )
    globex_api.post("/monitors/", {**MONITOR_PAYLOAD, "name": "api"}, format="json")

    assert made.status_code == 201
    assert [m["name"] for m in acme_api.get("/monitors/").json()["results"]] == ["web"]
    assert [m["name"] for m in globex_api.get("/monitors/").json()["results"]] == [
        "api"
    ]


def test_the_same_id_in_two_organizations_is_two_monitors(acme, alice, globex, gina):
    # Explicit ids: sequences are per schema and not rolled back between tests.
    create_monitor(owner=alice, id=1000, name="web")
    with globex:
        create_monitor(owner=gina, id=1000, name="api")

    assert client_for(alice, "acme").get("/monitors/1000/").json()["name"] == "web"
    assert client_for(gina, "globex").get("/monitors/1000/").json()["name"] == "api"


def test_a_non_member_gets_404_and_no_token_gets_401(alice, gina):
    assert client_for(gina, "acme").get("/monitors/").status_code == 404
    assert TenantClient("acme").get("/monitors/").status_code == 401
    assert client_for(alice, "nope").get("/monitors/").status_code == 404


@pytest.mark.parametrize(
    ("middleware", "queries"),
    [
        # SET public, the Domain lookup, SET the tenant, then today's three.
        ("django_tenants.middleware.TenantSubfolderMiddleware", 6),
        # slug -> tenant cached in the process: one SET, then today's three.
        ("orgs.middleware.TenantMiddleware", 4),
    ],
)
def test_listing_monitors_in_a_tenant_takes_this_many_queries(
    alice, django_assert_num_queries, middleware, queries
):
    """Today GET /monitors/ takes 3: the token's user, COUNT, the page."""
    create_monitor(owner=alice, name="a")
    create_monitor(owner=alice, name="b")
    with override_settings(
        MIDDLEWARE=[middleware, "core.middleware.RequestIDMiddleware"]
    ):
        client = client_for(alice, "acme")
        client.get("/monitors/")  # warms the process cache when there is one
        with django_assert_num_queries(queries):
            response = client.get("/monitors/")

    assert len(response.json()["results"]) == 2


@pytest.mark.parametrize(
    ("middleware", "queries"),
    [
        # Outside /t/ the stock middleware loads the public tenant row.
        ("django_tenants.middleware.TenantSubfolderMiddleware", 2),
        ("orgs.middleware.TenantMiddleware", 0),
    ],
)
def test_health_asks_the_database_unless_public_paths_skip_it(
    anon, django_assert_num_queries, middleware, queries
):
    with override_settings(MIDDLEWARE=[middleware]):
        with django_assert_num_queries(queries):
            assert TenantClient().get("/health").status_code == 200


def test_a_task_queued_in_a_tenant_runs_in_that_tenant(globex, fake_check, redis_db):
    with globex:
        monitor = create_monitor()
    use_public()

    tasks.run_check.apply(
        args=(monitor.pk,), headers={"_schema_name": globex.schema_name}
    )

    with globex:
        assert Check.objects.filter(monitor_id=monitor.pk).count() == 1


def test_a_task_without_a_tenant_fails_instead_of_reading_another(
    acme, fake_check, redis_db
):
    monitor = create_monitor()
    use_public()

    with pytest.raises(ProgrammingError, match='relation "monitors" does not exist'):
        tasks.run_check(monitor.pk)


def test_the_tick_claims_due_monitors_in_every_tenant(acme, globex, monkeypatch):
    in_acme = create_monitor(name="a")
    with globex:
        in_globex = create_monitor(name="g")
    queued: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_check,
        "apply_async",
        lambda **kw: queued.append((current_schema(), kw["args"][0])),
    )

    assert tasks.enqueue_due_checks() == 2
    assert sorted(queued) == [
        (acme.schema_name, in_acme.pk),
        (globex.schema_name, in_globex.pk),
    ]


def test_check_locks_are_per_tenant(acme, globex, fake_check, redis_db):
    monitor = create_monitor(id=1000)
    with globex:
        create_monitor(id=1000)
    locks.acquire(f"lock:{globex.schema_name}:monitor:1000", 60)

    tasks.run_check(monitor.pk)  # acme's monitor: globex's lock does not stop it

    assert Check.objects.filter(monitor=monitor).count() == 1


def test_cache_keys_are_per_tenant(acme, globex):
    cache.set("status:page", "acme's page")
    with globex:
        assert cache.get("status:page") is None
    assert cache.get("status:page") == "acme's page"


def test_monitors_live_only_in_tenant_schemas(acme):
    use_public()
    with pytest.raises(ProgrammingError):
        Monitor.objects.count()
