"""Celery tasks: thin wrappers that load a row and call the service the API uses.

Arguments cross the broker as JSON, so tasks take ids, never model instances, and
load the row fresh: it may have changed since the message was queued.
"""

from typing import Any

import httpx
from celery import Task, shared_task
from celery.utils.log import get_task_logger
from django.conf import settings

from core.system_checks import MAX_ALERT_ATTEMPTS

from . import alerting, locks, services
from .models import MAX_TIMEOUT_S, Alert, Monitor

log = get_task_logger(__name__)

# httpx applies timeout_s to each phase, so connecting and then waiting for the
# answer can take it twice over. Celery kills a check at the limit, and the lock
# outlives the limit, so a lock can never expire under a check that is still running.
CHECK_TIME_LIMIT_S = 2 * MAX_TIMEOUT_S + 15
CHECK_LOCK_TTL_S = CHECK_TIME_LIMIT_S + 15


@shared_task
def enqueue_due_checks() -> int:
    """Queue one run_check per due monitor. Beat calls this."""
    due = services.claim_due_monitors()
    for monitor_id, interval_s in due:
        # Not picked up within its interval: the next slot's message replaces it.
        run_check.apply_async(args=(monitor_id,), expires=interval_s)
    log.info("queued %d checks", len(due))
    return len(due)


@shared_task(time_limit=CHECK_TIME_LIMIT_S)
def run_check(monitor_id: int) -> None:
    """Check one monitor, unless another worker is checking it right now."""
    key = f"lock:monitor:{monitor_id}"
    token = locks.acquire(key, CHECK_LOCK_TTL_S)
    if token is None:
        log.info("monitor %d is already being checked, skipping", monitor_id)
        return
    try:
        _check(monitor_id)
    finally:
        if not locks.release(key, token):
            log.warning(
                "monitor %d: the lock expired before the check finished", monitor_id
            )


def _check(monitor_id: int) -> None:
    try:
        monitor = Monitor.objects.get(pk=monitor_id)
    except Monitor.DoesNotExist:
        log.info("monitor %d was deleted before its check ran", monitor_id)
        return
    result = services.check_monitor(monitor)
    log.info(
        "monitor %d ok=%s error=%s", monitor_id, result["ok"], result["error_type"]
    )


@shared_task(bind=True, max_retries=MAX_ALERT_ATTEMPTS - 1)
def send_alert(self: Task[Any, Any], alert_id: int) -> None:
    """Deliver one alert; retry with exponential backoff while the receiver fails.

    At-least-once: a second run for an alert already sent stops, but an attempt the
    receiver stored and answered too late is retried and arrives twice.
    Only the receiver failing is a task failure: a down target never is.
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
        max_attempts: int = settings.PULSE_ALERT_MAX_ATTEMPTS
        if alert.attempts >= max_attempts:
            alerting.mark_failed(alert)
            log.error(
                "alert %d failed after %d attempts: %r", alert_id, alert.attempts, exc
            )
            return
        countdown = alerting.retry_delay(alert.attempts)
        log.warning(
            "alert %d attempt %d failed (%r); retrying in %.1f s",
            alert_id,
            alert.attempts,
            exc,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
    log.info("alert %d sent: %s", alert_id, alert.kind)
