from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from . import tokens
from .models import User

KEYWORD = "Bearer"


class JWTAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>` becomes request.user.

    No such header: None, and IsAuthenticated answers 401. A header that is there
    but wrong raises, so a bad token is never treated as "not logged in".
    """

    def authenticate(self, request: Request) -> tuple[User, None] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != KEYWORD.lower().encode():
            return None
        user = self._user(parts[1:])
        if user is None:
            # One message: which check failed is nothing a caller needs to know.
            raise AuthenticationFailed("Invalid or expired token.")
        return user, None

    def authenticate_header(self, request: Request) -> str:
        # Without this DRF answers a missing or bad token with 403 instead of 401.
        return KEYWORD

    @staticmethod
    def _user(credentials: list[bytes]) -> User | None:
        if len(credentials) != 1:
            return None
        try:
            user_id = tokens.verify(credentials[0].decode())
        except (tokens.InvalidToken, UnicodeError):
            return None
        # A token outlives its user: an account deleted since login is rejected too.
        return User.objects.filter(pk=user_id).first()
