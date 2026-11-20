"""make migrate: public, the template, then every organization, stopping at the
first schema that fails and naming it (TEN-5)."""

import re
import threading
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django_tenants.signals import schema_pre_migration

from orgs import services
from orgs.models import Organization

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent.parent


def test_migrating_stops_at_the_organization_that_fails_and_names_it():
    broken, after = Organization.objects.bulk_create(
        [
            # A row whose schema is gone: migrating it fails.
            Organization(name="Broken", slug="broken", schema_name="org_broken"),
            Organization(name="After", slug="after", schema_name="org_after"),
        ]
    )
    started: list[str] = []

    def note(schema_name: str, **kwargs: object) -> None:
        started.append(schema_name)

    schema_pre_migration.connect(note, weak=False)
    try:
        with pytest.raises(CommandError, match="Migrating org_broken failed"):
            call_command("migrate_all", "--no-input", verbosity=0)
    finally:
        schema_pre_migration.disconnect(note)

    assert started[:2] == ["public", services.TEMPLATE_SCHEMA]
    assert "org_acme" in started and "org_globex" in started
    assert started[-1] == "org_broken"
    assert after.schema_name not in started


def test_make_migrate_runs_this_command():
    makefile = (ROOT / "Makefile").read_text()

    assert re.search(r"^migrate:.*\n\t.*manage\.py migrate_all$", makefile, re.M)


def test_migrating_the_template_holds_its_lock_the_whole_time(monkeypatch):
    """While it is held, a clone waits: tests/orgs/test_organizations.py."""
    inside, finish = threading.Event(), threading.Event()

    def migrating(*args, **kwargs):
        inside.set()
        finish.wait(10)

    monkeypatch.setattr(services, "call_command", migrating)

    def run() -> None:
        try:
            services.migrate_template()
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert inside.wait(5)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND objid = %s AND mode = 'ExclusiveLock' AND granted"
                " AND pid <> pg_backend_pid()",
                [services.TEMPLATE_LOCK],
            )
            (held,) = cursor.fetchone()
    finally:
        finish.set()
        thread.join(10)

    assert held == 1
