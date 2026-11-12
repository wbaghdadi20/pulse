"""Alerts: one signed webhook per incident event, delivered at least once, recorded once.

queue_alert() records the event and queues delivery; deliver() does one attempt.
Retrying is the task's job, so the policy lives next to the thing that applies it.
"""

import hashlib
import hmac
import json
import random
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.db import violated_constraint

from . import clients
from .models import Alert, Incident

SIGNATURE_HEADER = "X-Pulse-Signature"
_rng = random.Random()


def queue_alert(incident: Incident, kind: str) -> Alert | None:
    """Record that this event needs an alert and queue its delivery. None if already recorded."""
    try:
        with transaction.atomic():
            alert = Alert.objects.create(incident=incident, kind=kind)
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_alert_incident_kind":
            return None
        raise
    from .tasks import send_alert  # tasks imports this module: bind at call time

    # Only after COMMIT: a worker that picks the message up before the row is
    # visible would find nothing to send.
    transaction.on_commit(lambda: send_alert.delay(alert.pk))
    return alert


def deliver(alert: Alert) -> None:
    """POST the alert once. Raises httpx.HTTPError if the receiver is down or answers non-2xx."""
    # Count the attempt before trying, so a crash mid-request still counts as one.
    Alert.objects.filter(pk=alert.pk).update(attempts=F("attempts") + 1)
    alert.refresh_from_db(fields=["attempts"])

    body = json.dumps(
        payload_for(alert), sort_keys=True, separators=(",", ":")
    ).encode()
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(body)}
    response = clients.webhook_client().post(
        settings.PULSE_WEBHOOK_URL, content=body, headers=headers
    )
    response.raise_for_status()

    Alert.objects.filter(pk=alert.pk).update(
        status=Alert.Status.SENT, sent_at=timezone.now()
    )


def retry_delay(attempt: int, rng: random.Random = _rng) -> float:
    """Seconds to wait after a failed attempt: 2**attempt plus up to the same again.

    The exponential part gives a struggling receiver time to recover. The random
    part is jitter: when the receiver dies, every pending alert fails in the same
    second, and without it they would all retry in the same second too, four times.
    """
    floor = float(2**attempt)
    return floor + rng.uniform(0, floor)


def mark_failed(alert: Alert) -> None:
    Alert.objects.filter(pk=alert.pk).update(status=Alert.Status.FAILED)


def payload_for(alert: Alert) -> dict[str, Any]:
    incident = alert.incident
    resolved_at = incident.resolved_at
    downtime = (
        None
        if resolved_at is None
        else int((resolved_at - incident.opened_at).total_seconds())
    )
    return {
        "alert_id": alert.pk,
        "event": f"incident.{alert.kind}",
        "monitor": incident.monitor.name,
        "monitor_id": incident.monitor_id,
        "incident_id": incident.pk,
        "incident_kind": incident.kind,
        "cause": incident.cause,
        "opened_at": incident.opened_at.isoformat(),
        "resolved_at": None if resolved_at is None else resolved_at.isoformat(),
        "downtime_seconds": downtime,
    }


def sign(body: bytes) -> str:
    """HMAC-SHA256 over the exact bytes sent; the catcher recomputes it over the bytes received."""
    secret: str = settings.PULSE_WEBHOOK_SECRET
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
