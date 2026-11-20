"""Creating an organization: its row, its schema cloned from the template and its
Domain, all in the caller's transaction (TEN-1, docs/adr/0026). What signup adds,
the account and its owner membership: tests/accounts/test_signup_organization.py"""

import time

import pytest
from django.db import ProgrammingError, connection, transaction
from django_tenants.clone import CloneSchema
from django_tenants.utils import schema_exists

from orgs import services
from orgs.errors import (
    InvalidSlug,
    OrganizationsBusy,
    SlugChanged,
    SlugTaken,
    TemplateNotReady,
)
from orgs.models import Domain, Organization
from tests.orgs.test_slugs import HOSTILE

pytestmark = pytest.mark.django_db


def nothing_was_created(slug: str = "initech") -> bool:
    return not Organization.objects.filter(slug=slug).exists() and not schema_exists(
        f"org_{slug}"
    )


def test_an_organization_comes_with_its_schema_and_its_domain(db):
    org = services.create_organization("Initech", "initech")

    assert (org.name, org.slug, org.schema_name) == (
        "Initech",
        "initech",
        "org_initech",
    )
    assert Domain.objects.get(tenant=org).domain == "initech"
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('org_initech.monitors')")
        assert cursor.fetchone()[0] is not None


@pytest.mark.parametrize("slug", ["acme", "globex"])
def test_a_taken_slug_is_refused(slug):
    with pytest.raises(SlugTaken):
        services.create_organization("Again", slug)


def test_a_slug_is_taken_whatever_wrote_the_existing_schema(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA org_leftover")

    with pytest.raises(SlugTaken):
        services.create_organization("Leftover", "leftover")


def test_the_schema_failing_to_be_created_leaves_no_organization(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("clone failed")

    monkeypatch.setattr(CloneSchema, "clone_schema", broken)

    with pytest.raises(RuntimeError, match="clone failed"):
        services.create_organization("Initech", "initech")

    assert nothing_was_created()


def test_a_failure_after_the_clone_takes_the_schema_back_too(monkeypatch):
    def fails(schema):
        raise RuntimeError("renames failed")

    monkeypatch.setattr(services, "restore_constraint_names", fails)

    with pytest.raises(RuntimeError, match="renames failed"):
        services.create_organization("Initech", "initech")

    assert nothing_was_created()


def test_a_later_failure_in_the_callers_transaction_takes_it_all_back(db):
    with pytest.raises(RuntimeError), transaction.atomic():
        services.create_organization("Initech", "initech")
        raise RuntimeError("the caller's next step failed")

    assert nothing_was_created()


def test_the_clones_own_error_is_the_one_raised(monkeypatch):
    def broken(self, base, new, mode="DATA", set_connection=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM no_such_table_in_the_clone")

    monkeypatch.setattr(CloneSchema, "clone_schema", broken)

    with pytest.raises(ProgrammingError, match="no_such_table_in_the_clone"):
        services.create_organization("Broken", "broken")


def test_a_template_that_is_behind_the_code_is_never_cloned(monkeypatch):
    on_disk = services._tenant_migrations()
    monkeypatch.setattr(
        services,
        "_tenant_migrations",
        lambda: on_disk | {("monitors", "9999_not_applied")},
    )

    with pytest.raises(TemplateNotReady) as refused:
        services.create_organization("Initech", "initech")

    assert "lacks [('monitors', '9999_not_applied')]" in refused.value.detail

    assert nothing_was_created()


def test_a_template_that_is_ahead_of_the_code_is_never_cloned(monkeypatch):
    """Old code still serving, after a deploy's migrate_all migrated the template."""
    newest = max(services._tenant_migrations())
    older_code = services._tenant_migrations() - {newest}
    monkeypatch.setattr(services, "_tenant_migrations", lambda: older_code)

    with pytest.raises(TemplateNotReady) as refused:
        services.create_organization("Initech", "initech")

    assert f"has [{newest!r}]" in refused.value.detail

    assert nothing_was_created()


def test_a_clone_waits_for_a_template_migration_and_then_gives_up(
    template_being_migrated,
):
    started = time.monotonic()

    with pytest.raises(OrganizationsBusy):
        services.create_organization("Initech", "initech")

    assert 0.15 < time.monotonic() - started < 2, "the lock timeout, not forever"
    assert nothing_was_created()


@pytest.mark.parametrize("slug", HOSTILE)
def test_a_hostile_slug_is_refused_before_any_sql(slug, django_assert_num_queries):
    with django_assert_num_queries(0), pytest.raises(InvalidSlug):
        services.create_organization("Evil", slug)


def test_a_slug_never_changes(acme):
    acme.slug = "acme-two"

    with pytest.raises(SlugChanged):
        acme.save()


def test_a_slug_never_changes_on_the_instance_that_created_it_either(db):
    org = services.create_organization("Fresh", "fresh")
    org.slug = "fresh-two"

    with pytest.raises(SlugChanged):
        org.save()
