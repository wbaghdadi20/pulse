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
