"""Celery tasks: thin wrappers that load a row and call the service the API uses.

Arguments cross the broker as JSON, so tasks take ids, never model instances, and
load the row fresh: it may have changed since the message was queued.
"""

from datetime import timedelta
from typing import Any

import httpx
from celery import Task, shared_task
from celery.utils.log import get_task_logger
from django.db.models import DateTimeField, ExpressionWrapper, F, Q
from django.utils import timezone

from . import alerting, services
from .models import Alert, Monitor

log = get_task_logger(__name__)


@shared_task
def enqueue_due_checks() -> int:
    """Queue one run_check per monitor whose interval has elapsed. Beat calls this."""
    due_at = ExpressionWrapper(
        F("last_checked_at") + F("interval_s") * timedelta(seconds=1),
        output_field=DateTimeField(),
    )
    due = Monitor.objects.annotate(due_at=due_at).filter(
        Q(last_checked_at__isnull=True) | Q(due_at__lte=timezone.now())
    )
    ids = list(due.values_list("id", flat=True))
    for monitor_id in ids:
        run_check.delay(monitor_id)
    log.info("queued %d checks", len(ids))
    return len(ids)


@shared_task
def run_check(monitor_id: int) -> None:
    """The scheduled "check now": same service, no HTTP request in between."""
    try:
        monitor = Monitor.objects.get(pk=monitor_id)
    except Monitor.DoesNotExist:
        log.info("monitor %d was deleted before its check ran", monitor_id)
        return
    result = services.check_monitor(monitor)
    log.info(
        "monitor %d ok=%s error=%s", monitor_id, result["ok"], result["error_type"]
    )


@shared_task(bind=True, max_retries=alerting.MAX_ATTEMPTS - 1)
def send_alert(self: Task[Any, Any], alert_id: int) -> None:
    """Deliver one alert at most once; retry with exponential backoff while the receiver fails.

    Idempotent: a message that arrives twice (a retry after a timeout where the first
    attempt did get through, or a duplicate queueing) finds the row already sent and
    stops. Only the receiver failing is a task failure: a down target never is.
    """
    try:
        alert = Alert.objects.select_related("incident__monitor").get(pk=alert_id)
    except Alert.DoesNotExist:
        log.info("alert %d no longer exists", alert_id)
        return
    if alert.status != Alert.Status.PENDING:
        log.info("alert %d is already %s, nothing to do", alert_id, alert.status)
        return
    try:
        alerting.deliver(alert)
    except httpx.HTTPError as exc:
        if alert.attempts >= alerting.MAX_ATTEMPTS:
            alerting.mark_failed(alert)
            log.error(
                "alert %d failed after %d attempts: %r", alert_id, alert.attempts, exc
            )
            return
        countdown = 2**alert.attempts  # 2, 4, 8, 16 s
        log.warning(
            "alert %d attempt %d failed (%r); retrying in %d s",
            alert_id,
            alert.attempts,
            exc,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
    log.info("alert %d sent: %s", alert_id, alert.kind)
