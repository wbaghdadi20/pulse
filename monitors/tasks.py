"""Celery tasks: thin wrappers that load a row and call the service the API uses.

Arguments cross the broker as JSON, so tasks take ids, never model instances, and
load the row fresh: it may have changed since the message was queued.
"""

from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db.models import DateTimeField, ExpressionWrapper, F, Q
from django.utils import timezone

from . import services
from .models import Monitor

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
