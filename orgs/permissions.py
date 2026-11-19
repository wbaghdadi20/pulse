from typing import TYPE_CHECKING

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from .tenancy import current_tenant_id

if TYPE_CHECKING:
    # As in core/throttling.py: rest_framework.views imports this module while loading.
    from rest_framework.views import APIView


class IsMember(BasePermission):
    """Inside /t/<slug>/, only members get past; everyone else sees 404, as if no such org."""

    def has_permission(self, request: Request, view: "APIView") -> bool:
        if not (request.user and request.user.is_authenticated):
            return False  # 401 from IsAuthenticated's side of the contract
        if current_tenant_id() is None:
            return True
        if getattr(request.user, "tenant_role", None) is None:
            raise NotFound()
        return True
