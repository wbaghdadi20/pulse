from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from orgs.models import Membership
from orgs.services import active_membership
from orgs.tenancy import request_schema

from . import tokens
from .models import User

KEYWORD = "Bearer"


class JWTAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>` becomes request.user and, inside /t/<slug>/,
    request.auth: the caller's active membership there, or None.

    No such header: None, and IsAuthenticated answers 401. A header that is there
    but wrong raises, so a bad token is never treated as "not logged in".
    """

    def authenticate(self, request: Request) -> tuple[User, Membership | None] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != KEYWORD.lower().encode():
            return None
        user_id = self._user_id(parts[1:])
        schema = request_schema(request._request)
        if user_id is not None and schema is not None:
            # A member: the user and the organization come with it, in one query.
            membership = active_membership(user_id, schema)
            if membership is not None:
                assert membership.user is not None, "active() has a user"
                request._request.tenant = membership.organization  # type: ignore[attr-defined]
                return membership.user, membership
        # A token outlives its user: an account deleted since login is rejected too.
        user = User.objects.filter(pk=user_id).first() if user_id is not None else None
        if user is None:
            # One message: which check failed is nothing a caller needs to know.
            raise AuthenticationFailed("Invalid or expired token.")
        return user, None  # inside a tenant, IsMember turns a non-member away

    def authenticate_header(self, request: Request) -> str:
        # Without this DRF answers a missing or bad token with 403 instead of 401.
        return KEYWORD

    @staticmethod
    def _user_id(credentials: list[bytes]) -> int | None:
        if len(credentials) != 1:
            return None
        try:
            return tokens.verify(credentials[0].decode())
        except (tokens.InvalidToken, UnicodeError):
            return None


def current_user(request: Request) -> User:
    """request.user as a User. IsAuthenticated has already turned anonymous callers away."""
    user = request.user
    assert isinstance(user, User)
    return user


def current_membership(request: Request) -> Membership:
    """The caller's membership in this organization. IsMember has already turned
    non-members away, so inside /t/<slug>/ there always is one."""
    membership = request.auth
    assert isinstance(membership, Membership)
    return membership
