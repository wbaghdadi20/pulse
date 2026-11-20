from typing import TYPE_CHECKING

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from .models import Membership
from .tenancy import request_schema

if TYPE_CHECKING:
    # As in core/throttling.py: rest_framework.views imports this module while loading.
    from rest_framework.views import APIView


class IsMember(BasePermission):
    """Inside /t/<slug>/, only an active member of that organization gets past. Anyone
    else gets 404, as if it did not exist. After IsAuthenticated: no token is a 401."""

    def has_permission(self, request: Request, view: "APIView") -> bool:
        schema = request_schema(request._request)
        if schema is None:
            return True
        membership = request.auth
        if (
            not isinstance(membership, Membership)
            or membership.organization.schema_name != schema
        ):
            raise NotFound()
        return True
