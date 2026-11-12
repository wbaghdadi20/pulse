"""Business-rule errors, shared by every app.

These know nothing about HTTP. Services raise them; the DRF exception handler
(core/exception_handler.py) turns them into status codes for API requests, and
Celery tasks can catch them as plain Python exceptions.
"""


class DomainError(Exception):
    """Base class for errors caused by breaking a business rule."""


class NotFoundError(DomainError):
    """The thing asked for doesn't exist."""


class ConflictError(DomainError):
    """The change clashes with existing data."""


class AuthenticationError(DomainError):
    """The caller did not prove who they are."""
