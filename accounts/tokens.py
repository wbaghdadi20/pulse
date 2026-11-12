"""Access tokens: a JWT signed with PULSE_JWT_SECRET that names a user until it expires."""

from datetime import datetime, timedelta

import jwt
from django.conf import settings
from django.utils import timezone


class InvalidToken(Exception):
    """Not ours, tampered with, expired, or missing a claim."""


# Fixed on both sides. Reading the algorithm from the token would let its sender pick it.
ALGORITHM = "HS256"


def issue(user_id: int, now: datetime | None = None) -> str:
    now = now or timezone.now()
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.PULSE_JWT_TTL_SECONDS),
    }
    return jwt.encode(claims, settings.PULSE_JWT_SECRET, algorithm=ALGORITHM)


def verify(token: str) -> int:
    """The user id a token was issued for. Raises InvalidToken for anything else."""
    try:
        claims = jwt.decode(
            token,
            settings.PULSE_JWT_SECRET,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise InvalidToken from exc
