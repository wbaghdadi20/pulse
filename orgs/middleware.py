"""The subfolder middleware without its per-request queries: slug -> tenant is cached
in the process (a slug never changes), and paths outside /t/ never ask the database."""

from typing import Any

from django.http import HttpRequest
from django_tenants.middleware import TenantSubfolderMiddleware

from .models import Organization
from .tenancy import PUBLIC_SCHEMA, use_public

PREFIX = "/t/"
_known: dict[str, tuple[int, str, str]] = {}


class TenantMiddleware(TenantSubfolderMiddleware):  # type: ignore[misc]
    def process_request(self, request: HttpRequest) -> Any:
        if request.path.startswith(PREFIX):
            return super().process_request(request)
        # /health must not depend on Postgres (docs/adr/0018): no public-tenant row lookup.
        use_public()
        request.tenant = Organization(schema_name=PUBLIC_SCHEMA)  # type: ignore[attr-defined]
        self.setup_url_routing(request, force_public=True)
        return None

    def get_tenant(self, domain_model: Any, hostname: str) -> Organization:
        if hostname not in _known:
            org = (
                domain_model.objects.select_related("tenant")
                .get(domain=hostname)
                .tenant
            )
            _known[hostname] = (org.pk, org.schema_name, org.slug)
        pk, schema_name, slug = _known[hostname]
        # A fresh instance per request: the middleware writes onto it.
        return Organization(pk=pk, schema_name=schema_name, slug=slug)
