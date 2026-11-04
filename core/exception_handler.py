from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler

from core.errors import ConflictError, NotFoundError


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This conflicts with an existing resource."
    default_code = "conflict"


def exception_handler(exc, context):
    """Map domain errors to HTTP errors in one place, then let DRF build the response."""
    if isinstance(exc, NotFoundError):
        exc = exceptions.NotFound(str(exc))
    elif isinstance(exc, ConflictError):
        exc = Conflict(str(exc))
    return drf_exception_handler(exc, context)
