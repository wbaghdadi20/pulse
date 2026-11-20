from typing import Any

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.errors import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    UnavailableError,
)


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This conflicts with an existing resource."
    default_code = "conflict"


class Unavailable(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Try again shortly."
    default_code = "unavailable"

    def __init__(self, detail: str, wait: int) -> None:
        super().__init__(detail)
        self.wait = wait  # DRF turns it into Retry-After


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Map domain errors to HTTP errors in one place, then let DRF build the response."""
    if isinstance(exc, NotFoundError):
        exc = exceptions.NotFound(str(exc))
    elif isinstance(exc, ConflictError):
        exc = Conflict(str(exc))
    elif isinstance(exc, AuthenticationError):
        exc = exceptions.AuthenticationFailed(str(exc))
    elif isinstance(exc, InvalidInputError):
        # The shape of a serializer's 400: {"<field>": ["<message>"]}.
        exc = exceptions.ValidationError({exc.field or "detail": [str(exc)]})
    elif isinstance(exc, UnavailableError):
        exc = Unavailable(str(exc), wait=exc.retry_after)
    return drf_exception_handler(exc, context)
