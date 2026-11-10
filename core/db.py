from typing import Any

from django.db import IntegrityError
from django.db.models import Aggregate, FloatField
from django.db.models.expressions import Combinable


class PercentileCont(Aggregate):
    """Postgres percentile_cont(fraction) WITHIN GROUP (ORDER BY expression).

    Django has no built-in percentile aggregate. Unlike avg() or count(), this one
    takes the column in a WITHIN GROUP clause, so it needs its own SQL template.
    """

    function = "percentile_cont"
    template = "%(function)s(%(fraction)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(
        self, expression: Combinable | str, fraction: float, **extra: Any
    ) -> None:
        if not 0 <= fraction <= 1:
            raise ValueError("fraction must be between 0 and 1")
        # float() guarantees only a number ever reaches the SQL template.
        super().__init__(expression, fraction=float(fraction), **extra)


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the database constraint behind an IntegrityError, if Postgres reported one.

    Django wraps the driver's error; psycopg's original is in __cause__ and carries
    the constraint name in .diag.
    """
    diag = getattr(exc.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None)
