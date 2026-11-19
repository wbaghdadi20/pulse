"""The subfolder middleware, with slug -> tenant cached in the process (a slug never changes)."""

from typing import Any

from django_tenants.middleware import TenantSubfolderMiddleware

from .models import Organization

_known: dict[str, tuple[int, str, str]] = {}


class TenantMiddleware(TenantSubfolderMiddleware):  # type: ignore[misc]
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
