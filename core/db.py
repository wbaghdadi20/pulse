from django.db import IntegrityError


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the database constraint behind an IntegrityError, if Postgres reported one.

    Django wraps the driver's error; psycopg's original is in __cause__ and carries
    the constraint name in .diag.
    """
    diag = getattr(exc.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None)
