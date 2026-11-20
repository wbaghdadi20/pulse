"""A new organization's schema is the template's, down to constraint and index names."""

import pytest
from django.db import connection

from orgs import services

pytestmark = pytest.mark.django_db

# Each object's name and definition, with the schema's own name taken out of it.
CATALOG = {
    "constraints": """
        SELECT cls.relname, con.conname, con.contype,
               replace(pg_get_constraintdef(con.oid), %(schema)s || '.', '')
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = con.connamespace
        WHERE ns.nspname = %(schema)s
    """,
    "indexes": """
        SELECT tablename, indexname, replace(indexdef, %(schema)s || '.', '')
        FROM pg_indexes WHERE schemaname = %(schema)s
    """,
    "columns": """
        SELECT table_name, column_name, data_type, is_nullable, is_identity,
               column_default
        FROM information_schema.columns WHERE table_schema = %(schema)s
    """,
}


def catalog(schema: str) -> dict[str, set[tuple[object, ...]]]:
    found = {}
    with connection.cursor() as cursor:
        for kind, query in CATALOG.items():
            cursor.execute(query, {"schema": schema})
            found[kind] = set(cursor.fetchall())
    return found


def test_a_new_organizations_schema_matches_the_template():
    org = services.create_organization("Catalog", "catalog")

    template, cloned = catalog(services.TEMPLATE_SCHEMA), catalog(org.schema_name)

    assert cloned == template
    names = {name for _, name, *_ in cloned["constraints"]}
    assert {"uq_monitor_name", "uq_alert_incident_kind"} <= names


def test_constraint_names_are_restored_after_the_clone():
    """Without the renames the clone function's names would stand, and a duplicate
    monitor name would be a 500 in every new organization."""
    org = services.create_organization("Renamed", "renamed")

    assert services.restore_constraint_names(org.schema_name) == 0, "already done"
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE org_renamed.monitors RENAME CONSTRAINT uq_monitor_name TO x"
        )
    assert services.restore_constraint_names(org.schema_name) == 1
