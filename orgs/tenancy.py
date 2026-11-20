"""Typed access to what django-tenants adds to the database connection: which schema
it is in, and switching it. The library ships no type hints."""

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, cast

from django.db import connection
from django.http import HttpRequest
from django_tenants.utils import get_public_schema_name, schema_context

PUBLIC_SCHEMA: str = get_public_schema_name()

log = logging.getLogger(__name__)


def current_schema() -> str:
    return str(cast(Any, connection).schema_name)


def in_organization() -> bool:
    """The connection is in an organization's schema, not public."""
    return current_schema() != PUBLIC_SCHEMA


@contextmanager
def in_schema(schema: str) -> Iterator[None]:
    """Run the block in this schema, then go back to the one the connection was in.
    Already there: nothing changes, so no SET search_path is sent again."""
    if current_schema() == schema:
        yield
        return
    with schema_context(schema):
        yield


def in_public() -> AbstractContextManager[None]:
    return in_schema(PUBLIC_SCHEMA)


def request_schema(request: HttpRequest) -> str | None:
    """The organization's schema the middleware entered for this request; None
    outside /t/<slug>/."""
    schema: str | None = getattr(request, "tenant_schema", None)
    return schema


def in_each_organization[T](task: str, work: Callable[[], T]) -> list[T]:
    """Run `work` in every organization's schema. One failing does not stop the others:
    it is logged with its slug, and the task fails at the end, naming them all."""
    from .services import active_tenants  # services imports this module

    results: list[T] = []
    failed: list[str] = []
    for org in active_tenants():
        try:
            with org:
                results.append(work())
        except Exception:
            log.exception("%s failed in %s", task, org.slug)
            failed.append(org.slug)
    if failed:
        raise RuntimeError(f"{task} failed in {', '.join(failed)}")
    return results
