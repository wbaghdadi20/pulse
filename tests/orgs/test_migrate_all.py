"""make migrate: public, the template, then every organization, stopping at the
first schema that fails and naming it (TEN-5)."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_tenants.signals import schema_pre_migration

from orgs import services
from orgs.models import Organization

pytestmark = pytest.mark.django_db


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
