"""Alerts: one signed webhook per incident event, delivered at least once, recorded once.

queue_alert() is the write half: insert the alerts row (the unique constraint makes a
second call for the same event a no-op) and queue send_alert once the surrounding
transaction commits. deliver() is the I/O half the task calls: POST the signed
payload and mark the row sent. Retrying is the task's job, not this module's.
"""

import hashlib
import hmac
import json
from typing import Any

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.db import violated_constraint

from .models import Alert, Incident

MAX_ATTEMPTS = 5
SIGNATURE_HEADER = "X-Pulse-Signature"


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
    with new_client() as client:
        response = client.post(
            settings.PULSE_WEBHOOK_URL, content=body, headers=headers
        )
    response.raise_for_status()

    Alert.objects.filter(pk=alert.pk).update(
        status=Alert.Status.SENT, sent_at=timezone.now()
    )


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


def new_client() -> httpx.Client:
    return httpx.Client(timeout=5)
