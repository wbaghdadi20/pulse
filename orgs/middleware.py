"""/t/<slug>/... runs in that organization's schema, everything else in public: why
Pulse's own and not django-tenants' middleware, docs/adr/0026-schema-per-tenant.md."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django_tenants.urlresolvers import get_subfolder_urlconf
from django_tenants.utils import schema_context

from . import slugs
from .models import Domain
from .tenancy import PUBLIC_SCHEMA, in_public

PREFIX = f"/{settings.TENANT_SUBFOLDER_PREFIX}/"


@dataclass(frozen=True)
class _Tenant:
    schema_name: str
    # The tenant URLs under /t/<slug>/, built once so Django's resolver is too.
    urlconf: ModuleType


# slug -> schema: a slug never changes, nor does its schema. Nothing else is kept.
_known: dict[str, _Tenant] = {}


def forget_for_tests(keep: Iterable[str] = ()) -> None:
    """Test hook: organizations made in a test go with its transaction."""
    for slug in set(_known) - set(keep):
        del _known[slug]


def slug_in(path: str) -> str | None:
    if not path.startswith(PREFIX):
        return None
    return path[len(PREFIX) :].split("/", 1)[0]


def tenant_for(slug: str) -> _Tenant | None:
    if slug not in _known:
        # Checked first: a slug that cannot exist costs no query.
        if slugs.problem(slug) is not None:
            return None
        with in_public():
            schema_name = (
                Domain.objects.filter(domain=slug)
                .values_list("tenant__schema_name", flat=True)
                .first()
            )
        if schema_name is None:
            return None  # not remembered: unknown slugs would grow the dict forever
        urlconf = get_subfolder_urlconf(SimpleNamespace(domain_subfolder=slug))
        _known[slug] = _Tenant(schema_name, urlconf)
    return _known[slug]


class TenantMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        slug = slug_in(request.path)
        if slug is None:
            request.urlconf = settings.PUBLIC_SCHEMA_URLCONF
            with schema_context(PUBLIC_SCHEMA):
                return self.get_response(request)
        tenant = tenant_for(slug)
        if tenant is None:
            raise Http404("No such organization.")
        # Read by authentication and IsMember: which organization the request is for.
        request.tenant_schema = tenant.schema_name  # type: ignore[attr-defined]
        request.urlconf = tenant.urlconf  # type: ignore[assignment]  # a module works too
        # Entered afresh, as on the new connection of every request in production;
        # afterwards the connection goes back to the schema it was in.
        with schema_context(tenant.schema_name):
            return self.get_response(request)
