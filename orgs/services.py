"""Creating organizations. No HTTP in here."""

from django.db import connection

from accounts.models import User

from .models import Domain, Membership, Organization
from .tenancy import PUBLIC_SCHEMA, use_public


def schema_for(slug: str) -> str:
    # Slugs have no underscore, so "-" -> "_" cannot make two slugs collide.
    return "org_" + slug.replace("-", "_")


def ensure_public_tenant() -> Organization:
    """Subfolder mode refuses every request outside /t/ without this row."""
    org, _ = Organization.objects.get_or_create(
        schema_name=PUBLIC_SCHEMA, defaults={"name": "public", "slug": "public"}
    )
    assert isinstance(org, Organization)
    return org


def create_organization(
    name: str, slug: str, owner: User | None = None
) -> Organization:
    """The row, its schema (migrated or cloned, per settings), its slug, its owner."""
    use_public()
    org = Organization(name=name, slug=slug, schema_name=schema_for(slug))
    org.save(verbosity=0)
    restore_constraint_names(org.schema_name)
    Domain.objects.create(domain=slug, tenant=org, is_primary=True)
    if owner is not None:
        Membership.objects.create(user=owner, organization=org)
    return org


def active_tenants() -> list[Organization]:
    return list(Organization.objects.exclude(schema_name=PUBLIC_SCHEMA).order_by("id"))


def migrate_template() -> None:
    """Create or bring up to date the schema new organizations are cloned from."""
    from django.conf import settings
    from django.core.management import call_command

    template = settings.TENANT_BASE_SCHEMA
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{template}"')
    call_command(
        "migrate_schemas",
        tenant=True,
        schema_name=template,
        interactive=False,
        verbosity=0,
    )


# The clone function recreates UNIQUE constraints under Postgres's default names
# (uq_monitor_owner_name -> monitors_owner_id_name_key). Code and later migrations
# name them, so give each back the template's name.
_RENAMED_CONSTRAINTS = """
SELECT cls.relname, made.conname, orig.conname
FROM pg_constraint orig
JOIN pg_class orig_cls ON orig_cls.oid = orig.conrelid
JOIN pg_namespace orig_ns ON orig_ns.oid = orig.connamespace
JOIN pg_class cls ON cls.relname = orig_cls.relname
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
JOIN pg_constraint made ON made.conrelid = cls.oid
    AND made.contype = orig.contype
    AND pg_get_constraintdef(made.oid) = pg_get_constraintdef(orig.oid)
WHERE orig_ns.nspname = %s AND ns.nspname = %s
    AND orig.contype IN ('u', 'c', 'p', 'x') AND made.conname <> orig.conname
"""


def restore_constraint_names(schema: str) -> int:
    from django.conf import settings

    template = settings.TENANT_BASE_SCHEMA
    with connection.cursor() as cursor:
        cursor.execute(_RENAMED_CONSTRAINTS, [template, schema])
        renames = cursor.fetchall()
        for table, made, orig in renames:
            cursor.execute(
                f'ALTER TABLE "{schema}"."{table}" RENAME CONSTRAINT "{made}" TO "{orig}"'
            )
    return len(renames)
