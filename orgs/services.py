"""Organizations, their schemas and memberships. No HTTP in here.

A new organization's schema is cloned from a template schema that `make migrate`
keeps up to date, inside the caller's transaction: why, docs/adr/0026.
"""

from functools import cache
from typing import LiteralString

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from psycopg import errors, sql

from accounts.models import User
from core.db import violated_constraint

from . import slugs
from .errors import (
    InvalidSlug,
    NotAnActiveMember,
    OrganizationsBusy,
    SlugTaken,
    TemplateNotReady,
)
from .models import Domain, Membership, Organization
from .tenancy import PUBLIC_SCHEMA, current_schema, in_organization, in_public

TEMPLATE_SCHEMA: str = settings.TENANT_BASE_SCHEMA
SLUG_CONSTRAINTS = {"uq_organization_slug", "uq_organization_schema_name"}
# Held exclusively while the template is migrated and shared by every clone, so a
# clone never copies a template halfway through a migration.
TEMPLATE_LOCK = 7_011_984
# How long creating an organization waits for a lock before answering 503: clones
# take turns (docs/adr/0026), and a waiting signup holds a gunicorn worker.
LOCK_TIMEOUT: LiteralString = "2s"


def check_slug(slug: str) -> None:
    """Refuse a slug before it reaches any SQL: it becomes a schema name."""
    problem = slugs.problem(slug)
    if problem is not None:
        raise InvalidSlug(problem)


def create_organization(name: str, slug: str) -> Organization:
    """The row, its schema cloned from the template, and its Domain. In the caller's
    transaction if there is one: a later failure there takes the schema back too."""
    check_slug(slug)
    with in_public(), transaction.atomic():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '" + LOCK_TIMEOUT + "'")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock_shared(%s)", [TEMPLATE_LOCK]
                )
            check_template()
            org = _insert(name, slug)
            org.create_schema(verbosity=0)
        except OperationalError as exc:
            if isinstance(exc.__cause__, errors.LockNotAvailable):
                raise OrganizationsBusy from exc
            raise
        restore_constraint_names(org.schema_name)
        Domain.objects.create(domain=slug, tenant=org, is_primary=True)
    return org


def _insert(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, schema_name=slugs.schema_name(slug))
    try:
        with transaction.atomic():
            org.save(verbosity=0)
    except ValidationError as exc:
        # django-tenants checks the schema name before it inserts: taken by an
        # organization, or a schema of that name exists. Races reach the constraint.
        raise SlugTaken(slug) from exc
    except IntegrityError as exc:
        if violated_constraint(exc) in SLUG_CONSTRAINTS:
            raise SlugTaken(slug) from exc
        raise
    return org


def add_member(org: Organization, user: User, role: Membership.Role) -> Membership:
    return Membership.objects.create(organization=org, user=user, role=role)


def active_tenants() -> list[Organization]:
    """Every organization's tenant, oldest first. Ticket 3 leaves out deleted ones."""
    return list(Organization.objects.exclude(schema_name=PUBLIC_SCHEMA).order_by("id"))


def active_membership(user_id: int, schema: str) -> Membership | None:
    """The user's active membership in the organization of this schema, with the user
    and the organization: one query. Ticket 3 adds `deleted_at IS NULL` here."""
    return (
        Membership.objects.active()
        .select_related("user", "organization")
        .filter(user_id=user_id, organization__schema_name=schema)
        .first()
    )


def require_active_member(membership_id: int) -> None:
    """Refuse a membership that is not active in the organization the connection is in.

    Tenant rows reference memberships without a database constraint (no foreign key
    crosses schemas), so every service that stores one calls this first.
    """
    if (
        not in_organization()
        or not Membership.objects.active()
        .filter(pk=membership_id, organization__schema_name=current_schema())
        .exists()
    ):
        raise NotAnActiveMember(membership_id)


# The template


def migrate_template(verbosity: int = 0) -> None:
    """Create the template schema if it is missing, then apply what it lacks."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [TEMPLATE_LOCK])
    try:
        with connection.cursor() as cursor:
            cursor.execute(_compose("CREATE SCHEMA IF NOT EXISTS {}", TEMPLATE_SCHEMA))
        call_command(
            "migrate_schemas",
            tenant=True,
            schema_name=TEMPLATE_SCHEMA,
            interactive=False,
            verbosity=verbosity,
        )
    finally:
        # migrate_schemas closes its connection when done, which already let go.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [TEMPLATE_LOCK])


def check_template() -> None:
    """Refuse to clone a template whose tenant-app migrations are not exactly the
    code's. A clone's migrations are recorded from disk without running: from a
    template behind, the schema would lack one; from one ahead, it would hold one
    recorded as not applied, which the next migrate_all fails to apply again."""
    applied = _template_migrations()
    if applied is None:
        raise TemplateNotReady(f"No {TEMPLATE_SCHEMA} schema: make migrate")
    on_disk = _tenant_migrations()
    if applied != on_disk:
        raise TemplateNotReady(
            f"{TEMPLATE_SCHEMA} lacks {sorted(on_disk - applied)} and has "
            f"{sorted(applied - on_disk)} that this code does not: make migrate"
        )


def template_is_current() -> bool:
    return _template_migrations() == _tenant_migrations()


def _template_migrations() -> frozenset[tuple[str, str]] | None:
    """(app, name) of the tenant apps' migrations applied in the template; None if
    it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass(%s)",
            [_compose("{}.django_migrations", TEMPLATE_SCHEMA)],
        )
        (table,) = cursor.fetchone()
        if table is None:
            return None
        cursor.execute(
            _compose("SELECT app, name FROM {}.django_migrations", TEMPLATE_SCHEMA)
        )
        rows = cursor.fetchall()
    labels = _tenant_app_labels()
    return frozenset((app, name) for app, name in rows if app in labels)


def _tenant_app_labels() -> set[str]:
    return {
        config.label
        for config in apps.get_app_configs()
        if config.name in settings.TENANT_APPS
    }


@cache
def _tenant_migrations() -> frozenset[tuple[str, str]]:
    """(app, name) of every migration of the tenant apps, read from disk once."""
    graph = MigrationLoader(None, ignore_no_migrations=True).graph
    labels = _tenant_app_labels()
    return frozenset(node for node in graph.nodes if node[0] in labels)


# The clone function (pg-clone-schema, inside django-tenants) recreates UNIQUE
# constraints under Postgres's default names (uq_monitor_name -> monitors_name_key).
# The code maps violations to errors by name, so each gets the template's name back.
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
    """Give a cloned schema's constraints the template's names. Returns how many."""
    with connection.cursor() as cursor:
        cursor.execute(_RENAMED_CONSTRAINTS, [TEMPLATE_SCHEMA, schema])
        renames = cursor.fetchall()
        for table, made, original in renames:
            cursor.execute(
                _compose(
                    "ALTER TABLE {}.{} RENAME CONSTRAINT {} TO {}",
                    schema,
                    table,
                    made,
                    original,
                )
            )
    return len(renames)


def _compose(template: LiteralString, *identifiers: str) -> str:
    """SQL with each {} an identifier, quoted by psycopg: never an f-string."""
    return sql.SQL(template).format(*map(sql.Identifier, identifiers)).as_string()
