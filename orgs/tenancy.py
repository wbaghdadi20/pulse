"""Typed access to what django-tenants adds to the connection (the library ships no types)."""

from typing import Any, cast

from django.db import connection

PUBLIC_SCHEMA = "public"


def _db() -> Any:
    return cast(Any, connection)


def current_schema() -> str:
    return str(_db().schema_name)


def current_tenant_id() -> int | None:
    """The organization the connection is in; None in public or a bare schema."""
    if current_schema() == PUBLIC_SCHEMA:
        return None
    pk = getattr(_db().tenant, "pk", None)
    return int(pk) if pk is not None else None


def use_public() -> None:
    _db().set_schema_to_public()
