"""Business logic for monitors.

No HTTP in here: views call these functions today, Celery tasks will from D6.
Convention: get_monitor() turns an id into a monitor (or raises MonitorNotFound);
every other function takes the monitor itself.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, FloatField, IntegerField, QuerySet
from django.db.models.functions import Cast
from django.utils import timezone

from core.db import PercentileCont, violated_constraint

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


def get_uptime(monitor: Monitor, days: int) -> dict:
    """Uptime and p95 latency over the last `days` days, computed by Postgres.

    One aggregate query: no check rows are loaded into Python.
    """
    since = timezone.now() - timedelta(days=days)
    stats = Check.objects.filter(monitor=monitor, checked_at__gt=since).aggregate(
        # avg(ok::int): true counts as 1, false as 0, so the average is the up ratio.
        up_ratio=Avg(Cast("ok", IntegerField()), output_field=FloatField()),
        p95_latency_ms=PercentileCont("latency_ms", 0.95),
        checks=Count("id"),
    )
    no_checks = stats["checks"] == 0
    return {
        "days": days,
        "uptime_percent": None if no_checks else round(stats["up_ratio"] * 100, 2),
        "p95_latency_ms": None if no_checks else round(stats["p95_latency_ms"]),
        "checks": stats["checks"],
    }


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
