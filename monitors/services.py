"""Business logic for monitors.

No HTTP in here: views call these functions today, Celery tasks will from D6.
Convention: get_monitor() turns an id into a monitor (or raises MonitorNotFound);
every other function takes the monitor itself.
"""

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from core.db import violated_constraint

from .checks import run_check
from .errors import MonitorNameTaken, MonitorNotFound
from .models import Check, Monitor


def list_monitors() -> QuerySet[Monitor]:
    # Lazy: the paginator adds COUNT(*) and LIMIT/OFFSET before anything runs.
    return Monitor.objects.order_by("id")


def get_monitor(pk: int) -> Monitor:
    try:
        return Monitor.objects.get(pk=pk)
    except Monitor.DoesNotExist:
        raise MonitorNotFound(pk) from None


def create_monitor(data: dict) -> Monitor:
    monitor = Monitor(**data)
    _save(monitor)
    return monitor


def update_monitor(monitor: Monitor, changes: dict) -> Monitor:
    for field, value in changes.items():
        setattr(monitor, field, value)
    _save(monitor, update_fields=list(changes))
    return monitor


def delete_monitor(monitor: Monitor) -> None:
    monitor.delete()  # its checks and incidents go too (on_delete=CASCADE)


def check_monitor(monitor: Monitor) -> dict:
    result = run_check(monitor)
    Check.objects.create(monitor=monitor, **result)
    return result


def _save(monitor: Monitor, **save_kwargs) -> None:
    """Save, turning a duplicate name into MonitorNameTaken.

    There is no "is the name free?" query first: two requests could both see "free".
    The unique constraint decides. atomic() wraps the INSERT/UPDATE in a savepoint,
    so a failure doesn't break a surrounding transaction (e.g. in tests).
    """
    try:
        with transaction.atomic():
            monitor.save(**save_kwargs)
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_monitor_name":
            raise MonitorNameTaken(monitor.name) from exc
        raise
