"""Two organizations side by side, each in its own schema (docs/adr/0026, TEN-2)."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import ProgrammingError, connection
from django.utils import timezone
from rest_framework.request import Request

from accounts import tokens
from accounts.authentication import JWTAuthentication
from accounts.models import User
from core import views
from monitors import alerting, locks, retention, services, status_page, tasks
from monitors.models import Check, CheckHour, Incident, Monitor
from orgs import middleware
from orgs.models import Membership, Organization
from orgs.tenancy import current_schema
from tests.conftest import (
    MONITOR_PAYLOAD,
    TenantClient,
    client_for,
    create_monitor,
    membership_of,
    use_public,
)

pytestmark = pytest.mark.django_db


def member_of(org: Organization, email: str) -> Membership:
    return membership_of(User.objects.create_user(email), org)


@pytest.fixture
def alice(acme) -> Membership:
    return member_of(acme, "alice@example.com")


@pytest.fixture
def gina(globex) -> Membership:
    return member_of(globex, "gina@example.com")


def token_of(membership: Membership, slug: str) -> Any:
    assert membership.user is not None
    return client_for(membership.user, slug)


# Requests


def test_each_organization_lists_and_creates_its_own_monitors(alice, gina):
    acme_api, globex_api = token_of(alice, "acme"), token_of(gina, "globex")

    made = acme_api.post("/monitors/", {**MONITOR_PAYLOAD, "name": "web"}, "json")
    globex_api.post("/monitors/", {**MONITOR_PAYLOAD, "name": "api"}, "json")

    assert made.status_code == 201
    assert [m["name"] for m in acme_api.get("/monitors/").json()["results"]] == ["web"]
    assert [m["name"] for m in globex_api.get("/monitors/").json()["results"]] == [
        "api"
    ]


def test_the_same_id_in_two_organizations_is_two_monitors(alice, globex, gina):
    # Explicit ids: sequences are per schema and not rolled back between tests.
    create_monitor(alice, id=1000, name="web")
    with globex:
        create_monitor(gina, id=1000, name="api")

    assert token_of(alice, "acme").get("/monitors/1000/").json()["name"] == "web"
    assert token_of(gina, "globex").get("/monitors/1000/").json()["name"] == "api"


def test_a_member_of_another_organization_gets_404(gina):
    assert token_of(gina, "acme").get("/monitors/").status_code == 404


def test_no_token_gets_401(db):
    assert TenantClient("acme").get("/monitors/").status_code == 401


def test_an_unknown_slug_gets_404_whatever_the_token(alice):
    assert token_of(alice, "nope").get("/monitors/").status_code == 404


def test_a_membership_whose_account_is_gone_lets_nobody_in(alice):
    """The token's user must be the membership's: SET_NULL leaves no way in."""
    client = token_of(alice, "acme")
    assert alice.user
    alice.user.delete()

    assert client.get("/monitors/").status_code == 401
    alice.refresh_from_db()
    assert alice.user_id is None


def test_the_request_knows_its_organization_from_the_membership_query(alice, rf):
    """request.tenant is the organization row the authentication loaded, whole."""
    assert alice.user
    request = rf.get("/", HTTP_AUTHORIZATION=f"Bearer {tokens.issue(alice.user.pk)}")
    request.tenant_schema = "org_acme"

    user, membership = JWTAuthentication().authenticate(Request(request)) or (
        None,
        None,
    )

    assert (user, membership) == (alice.user, alice)
    assert request.tenant.slug == "acme"
    assert request.tenant.name == "Acme"


def test_after_a_request_to_another_organization_the_connection_is_back(alice, gina):
    token_of(gina, "globex").get("/monitors/")

    assert current_schema() == "org_acme"


def test_a_public_path_runs_in_public_and_the_connection_comes_back(db, monkeypatch):
    seen: list[str] = []
    real = views.HealthView.get

    def get(self, request):
        seen.append(current_schema())
        return real(self, request)

    monkeypatch.setattr(views.HealthView, "get", get)
    TenantClient().get("/health")

    assert seen == ["public"]
    assert current_schema() == "org_acme"


def test_listing_monitors_takes_one_statement_more_than_before(
    alice, django_assert_num_queries
):
    """Before tenants: the token's user, COUNT, the page. Now first the SET search_path
    that enters acme, and the user comes with the membership and organization."""
    create_monitor(alice, name="a")
    create_monitor(alice, name="b")
    client = token_of(alice, "acme")

    with django_assert_num_queries(4):
        response = client.get("/monitors/")

    assert len(response.json()["results"]) == 2


@pytest.fixture
def acme_forgotten() -> Any:
    """The middleware has not yet seen acme; it is known again afterwards, pass or fail."""
    middleware.forget_for_tests(keep=["globex"])
    yield
    middleware.tenant_for("acme")


def test_the_first_request_to_a_slug_looks_it_up_and_the_next_ones_do_not(
    alice, acme_forgotten, django_assert_num_queries
):
    create_monitor(alice)
    client = token_of(alice, "acme")

    # SET public and the slug's lookup, then the four of every request.
    with django_assert_num_queries(6):
        client.get("/monitors/")
    with django_assert_num_queries(4):
        client.get("/monitors/")


def test_paths_outside_t_ask_the_database_nothing(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert TenantClient().get("/health").status_code == 200


def test_a_slug_that_cannot_exist_is_not_even_looked_up(api, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert api.get("/t/Not_A_Slug/monitors/").status_code == 404


def test_an_unknown_slug_gets_the_security_headers_too(api):
    response = api.get("/t/nope/monitors/")

    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


# Tasks


def test_a_task_queued_in_an_organization_runs_in_its_schema(
    globex, fake_check, redis_db
):
    with globex:
        monitor = create_monitor(None)
    use_public()

    tasks.run_check.apply(
        args=(monitor.pk,), headers={"_schema_name": globex.schema_name}
    )

    with globex:
        assert Check.objects.filter(monitor_id=monitor.pk).count() == 1


def test_a_task_without_an_organization_fails_instead_of_reading_another(
    fake_check, redis_db
):
    monitor = create_monitor()
    use_public()

    with pytest.raises(ProgrammingError, match='relation "monitors" does not exist'):
        tasks.run_check(monitor.pk)


def test_monitors_live_only_in_organization_schemas():
    use_public()

    with pytest.raises(ProgrammingError):
        Monitor.objects.count()


@pytest.fixture
def queued(monkeypatch) -> list[tuple[str, Any]]:
    """(schema, monitor id) of each run_check the tick queues, instead of queueing."""
    found: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_check,
        "apply_async",
        lambda **kw: found.append((current_schema(), kw["args"][0])),
    )
    return found


def test_the_tick_claims_due_monitors_in_every_organization(acme, globex, queued):
    in_acme = create_monitor(name="a")
    with globex:
        in_globex = create_monitor(None, name="g")

    assert tasks.enqueue_due_checks() == 2
    assert sorted(queued) == [
        (acme.schema_name, in_acme.pk),
        (globex.schema_name, in_globex.pk),
    ]
    assert current_schema() == acme.schema_name, "back where it started"


def test_one_organization_failing_does_not_stop_the_tick_for_the_others(
    globex, queued, monkeypatch, caplog
):
    create_monitor(name="a")
    with globex:
        in_globex = create_monitor(None, name="g")
    real = services.claim_due_monitors

    def broken_in_acme():
        if current_schema() == "org_acme":
            raise RuntimeError("acme's claim broke")
        return real()

    monkeypatch.setattr(services, "claim_due_monitors", broken_in_acme)

    with pytest.raises(RuntimeError, match="enqueue_due_checks failed in acme"):
        tasks.enqueue_due_checks()

    assert queued == [(globex.schema_name, in_globex.pk)]
    assert "enqueue_due_checks failed in acme" in caplog.text


def test_the_roll_up_sums_hours_in_every_organization(acme, globex):
    two_hours_ago = timezone.now() - timedelta(hours=2)
    for org in (acme, globex):
        with org:
            monitor = create_monitor(None)
            Check.objects.create(
                monitor=monitor,
                checked_at=two_hours_ago,
                ok=True,
                status_code=200,
                latency_ms=10,
            )

    tasks.roll_up_checks()

    assert CheckHour.objects.count() == 1
    with globex:
        assert CheckHour.objects.count() == 1


def test_one_organization_failing_does_not_stop_the_roll_up_for_the_others(
    globex, monkeypatch
):
    real = retention.roll_up
    rolled_in: list[str] = []

    def roll_up():
        if current_schema() == "org_acme":
            raise RuntimeError("acme broke")
        rolled_in.append(current_schema())
        return real()

    monkeypatch.setattr(retention, "roll_up", roll_up)

    with pytest.raises(RuntimeError, match="roll_up_checks failed in acme"):
        tasks.roll_up_checks()

    assert rolled_in == [globex.schema_name]


def test_check_locks_are_per_organization(globex, fake_check, redis_db):
    monitor = create_monitor(id=1000)
    with globex:
        create_monitor(None, id=1000)
        theirs = tasks.lock_key(1000)
    locks.acquire(theirs, 60)

    tasks.run_check(monitor.pk)  # acme's monitor: globex's lock does not stop it

    assert theirs != tasks.lock_key(1000)
    assert Check.objects.filter(monitor=monitor).count() == 1


def test_cache_keys_are_per_organization(globex):
    cache.set("status:page", "acme's page")
    with globex:
        assert cache.get("status:page") is None
    assert cache.get("status:page") == "acme's page"


def test_production_keys_the_cache_by_schema_and_rate_limits_by_client_alone():
    """The tests run on a local cache built from this same configuration."""
    caches = settings.CACHES

    assert caches["default"]["KEY_FUNCTION"] == "django_tenants.cache.make_key"
    assert "KEY_FUNCTION" not in caches["ratelimit"]


# What is queued after commit belongs to the organization it was queued in.


def test_an_alert_queued_in_globex_is_sent_in_globex(
    globex, monkeypatch, django_capture_on_commit_callbacks
):
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.send_alert, "apply_async", lambda args, headers: sent.append(headers)
    )
    with django_capture_on_commit_callbacks() as callbacks, globex:
        incident = Incident.objects.create(monitor=create_monitor(None))
        alerting.queue_alert(incident, "opened")
    assert current_schema() == "org_acme"

    for callback in callbacks:
        callback()

    assert sent == [{"_schema_name": globex.schema_name}]


def test_a_change_in_globex_drops_globexs_page_and_not_acmes(
    globex, django_capture_on_commit_callbacks
):
    cache.set(status_page.CACHE_KEY, "acme's page")
    with globex:
        cache.set(status_page.CACHE_KEY, "globex's page")
    with django_capture_on_commit_callbacks() as callbacks, globex:
        status_page.invalidate()

    for callback in callbacks:
        callback()

    assert cache.get(status_page.CACHE_KEY) == "acme's page"
    with globex:
        assert cache.get(status_page.CACHE_KEY) is None


# The catalog: what is in public, what is in each organization's schema.


def table_names(schema: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s", [schema]
        )
        return {name for (name,) in cursor.fetchall()}


def test_public_holds_the_shared_tables_and_the_template_the_tenant_ones():
    assert table_names("public") == {
        "django_migrations",
        "django_content_type",
        "auth_permission",
        "auth_group",
        "auth_group_permissions",
        "users",
        "organizations",
        "organization_domains",
        "memberships",
    }
    assert table_names("tenant_template") == {
        "django_migrations",
        "monitors",
        "checks",
        "check_hours",
        "incidents",
        "alerts",
    }


def test_no_foreign_key_crosses_from_an_organization_into_public(acme, globex):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ns.nspname, con.conname
            FROM pg_constraint con
            JOIN pg_namespace ns ON ns.oid = con.connamespace
            JOIN pg_class target ON target.oid = con.confrelid
            JOIN pg_namespace target_ns ON target_ns.oid = target.relnamespace
            WHERE con.contype = 'f' AND ns.nspname = ANY(%s)
              AND target_ns.nspname <> ns.nspname
            """,
            [["tenant_template", acme.schema_name, globex.schema_name]],
        )
        assert cursor.fetchall() == []
