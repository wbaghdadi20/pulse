"""Business logic for monitors.

No HTTP in here: views call these functions today, Celery tasks will from D6.
Convention: get_monitor() turns an id into a monitor (or raises MonitorNotFound);
every other function takes the monitor itself.
"""

from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import DateTimeField, ExpressionWrapper, F, QuerySet, Value
from django.utils import timezone

from core.db import violated_constraint

from . import alerting, incidents, status_page, uptime
from .checks import CheckResult, probe
from .errors import IncidentAlreadyOpen, MonitorNameTaken, MonitorNotFound
from .models import Alert, Check, Incident, Monitor


def list_monitors() -> QuerySet[Monitor]:
    # Lazy: the paginator adds COUNT(*) and LIMIT/OFFSET before anything runs.
    return Monitor.objects.order_by("id")


def get_monitor(pk: int) -> Monitor:
    try:
        return Monitor.objects.get(pk=pk)
    except Monitor.DoesNotExist:
        raise MonitorNotFound(pk) from None


def create_monitor(data: dict[str, Any]) -> Monitor:
    monitor = Monitor(**data)
    _save(monitor)
    return monitor


def update_monitor(monitor: Monitor, changes: dict[str, Any]) -> Monitor:
    for field, value in changes.items():
        setattr(monitor, field, value)
    _save(monitor, update_fields=list(changes))
    return monitor


def delete_monitor(monitor: Monitor) -> None:
    monitor.delete()  # its checks and incidents go too (on_delete=CASCADE)


def claim_due_monitors(now: datetime | None = None) -> list[tuple[int, int]]:
    """(id, interval_s) of every monitor that is due, each claimed for this caller.

    Claiming moves next_check_at one interval ahead in the same transaction that
    found the monitor due, so it is handed out once per interval however many
    callers ask. Rows another caller is claiming right now are skipped, not waited for.
    """
    now = now or timezone.now()
    next_slot = ExpressionWrapper(
        Value(now) + F("interval_s") * timedelta(seconds=1),
        output_field=DateTimeField(),
    )
    with transaction.atomic():
        due = list(
            Monitor.objects.select_for_update(skip_locked=True)
            .filter(next_check_at__lte=now)
            .values_list("id", "interval_s")
        )
        Monitor.objects.filter(pk__in=[pk for pk, _ in due]).update(
            next_check_at=next_slot
        )
    return due


def check_monitor(monitor: Monitor) -> CheckResult:
    """Check once, record the result, then let the state machine react to it."""
    result = probe(monitor)
    check = Check.objects.create(monitor=monitor, **result)
    Monitor.objects.filter(pk=monitor.pk).update(last_checked_at=check.checked_at)
    evaluate_incidents(monitor)
    return result


def evaluate_incidents(monitor: Monitor) -> incidents.Action:
    """Apply the state machine to the monitor's latest checks and act on the answer.

    This is the I/O half: load the recent results and the current state, ask
    incidents.decide() (pure), then open or resolve in the database.
    """
    if monitor.in_maintenance():
        return incidents.Action.NOTHING

    fail_threshold = settings.PULSE_FAIL_THRESHOLD
    ok_threshold = settings.PULSE_OK_THRESHOLD

    # Only the last max(N, M) checks can matter; fetch exactly those (LIMIT).
    window = max(fail_threshold, ok_threshold)
    latest = Check.objects.filter(monitor=monitor).order_by("-checked_at", "-id")[
        :window
    ]
    results = [check.ok for check in reversed(latest)]  # oldest first

    kind = Incident.Kind.AVAILABILITY
    # At most one exists: enforced by the partial unique index uq_one_open_incident_per_kind.
    current = Incident.objects.filter(
        monitor=monitor, kind=kind, resolved_at__isnull=True
    ).first()

    action = incidents.decide(
        results,
        incident_open=current is not None,
        fail_threshold=fail_threshold,
        ok_threshold=ok_threshold,
    )
    if action is incidents.Action.OPEN:
        try:
            open_incident_for(
                monitor, cause=f"{fail_threshold} failed checks in a row", kind=kind
            )
        except IncidentAlreadyOpen:
            # Another check of this monitor opened it between our read and our insert.
            return incidents.Action.NOTHING
    elif action is incidents.Action.RESOLVE:
        assert current is not None, "decide() only resolves when incident_open=True"
        resolve_incident(current)
    return action


def get_uptime(
    monitor: Monitor, days: int, now: datetime | None = None
) -> dict[str, Any]:
    """Uptime and p95 latency over the last `days` days, computed by Postgres.

    One aggregate query: no check rows are loaded into Python.
    `now` is injectable so tests can pin the clock; production leaves it None.
    """
    since = (now or timezone.now()) - timedelta(days=days)
    stats = Check.objects.filter(monitor=monitor, checked_at__gt=since).aggregate(
        **uptime.aggregates()
    )
    return {"days": days, **uptime.summarise(stats)}


def open_incident_for(
    monitor: Monitor, cause: str = "", kind: str = Incident.Kind.AVAILABILITY
) -> Incident:
    """Open an incident, unless one of this kind is already open for this monitor.

    Like the name check, there is no "is one open?" query first: the partial unique
    index uq_one_open_incident_per_kind decides, so two concurrent calls can't both succeed.
    """
    try:
        with transaction.atomic():
            incident = Incident.objects.create(monitor=monitor, kind=kind, cause=cause)
            alerting.queue_alert(incident, Alert.Kind.OPENED)
            status_page.invalidate()
            return incident
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_one_open_incident_per_kind":
            raise IncidentAlreadyOpen(monitor.pk, kind) from exc
        raise


def resolve_incident(incident: Incident, now: datetime | None = None) -> Incident:
    with transaction.atomic():
        incident.resolved_at = now or timezone.now()
        incident.save(update_fields=["resolved_at"])
        alerting.queue_alert(incident, Alert.Kind.RESOLVED)
        status_page.invalidate()
    return incident


def _save(monitor: Monitor, **save_kwargs: Any) -> None:
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
