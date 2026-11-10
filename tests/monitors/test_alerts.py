"""Alerts: one row per incident event, one delivery per row, retried while the receiver fails.

`webhook` stands in for the catcher: an httpx MockTransport that records every request
and answers with a chosen status. The task is called directly, so each call is one
attempt, and send_alert.retry is replaced to capture the backoff it asked for.
"""

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from django.conf import settings

from monitors import alerting, services, tasks
from monitors.models import Alert, Incident

pytestmark = pytest.mark.django_db


class Receiver:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)


@pytest.fixture
def webhook(monkeypatch) -> Receiver:
    receiver = Receiver()
    monkeypatch.setattr(
        alerting,
        "new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(receiver.handle)),
    )
    return receiver


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    """Alert ids send_alert.delay was called with, instead of queueing them."""
    ids: list[int] = []
    monkeypatch.setattr(tasks.send_alert, "delay", ids.append)
    return ids


@pytest.fixture
def incident(monitor) -> Incident:
    return Incident.objects.create(monitor=monitor, cause="test")


@pytest.fixture
def alert(incident) -> Alert:
    return Alert.objects.create(incident=incident, kind=Alert.Kind.OPENED)


# --- queueing ---------------------------------------------------------------------


def test_opening_an_incident_records_and_queues_one_opened_alert(
    monitor, queued, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        incident = services.open_incident_for(monitor)

    alert = Alert.objects.get(incident=incident)
    assert alert.kind == Alert.Kind.OPENED
    assert alert.status == Alert.Status.PENDING
    assert queued == [alert.pk]


def test_resolving_an_incident_queues_a_resolved_alert(
    incident, queued, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        services.resolve_incident(incident)

    assert list(Alert.objects.values_list("kind", flat=True)) == ["resolved"]
    assert len(queued) == 1


def test_the_same_event_is_recorded_once(incident, queued):
    first = alerting.queue_alert(incident, Alert.Kind.OPENED)
    second = alerting.queue_alert(incident, Alert.Kind.OPENED)

    assert first is not None
    assert second is None
    assert Alert.objects.filter(incident=incident).count() == 1


def test_the_alert_is_queued_only_after_commit(incident, queued):
    alerting.queue_alert(incident, Alert.Kind.OPENED)

    assert queued == []  # the test transaction never commits


# --- delivery ---------------------------------------------------------------------


def test_send_alert_posts_a_signed_payload_and_marks_the_row_sent(alert, webhook):
    tasks.send_alert(alert.pk)

    (request,) = webhook.requests
    assert str(request.url) == settings.PULSE_WEBHOOK_URL
    body = json.loads(request.content)
    assert body["event"] == "incident.opened"
    assert body["monitor"] == alert.incident.monitor.name
    assert body["resolved_at"] is None and body["downtime_seconds"] is None
    expected = hmac.new(
        settings.PULSE_WEBHOOK_SECRET.encode(), request.content, hashlib.sha256
    ).hexdigest()
    assert request.headers[alerting.SIGNATURE_HEADER] == expected
    alert.refresh_from_db()
    assert alert.status == Alert.Status.SENT
    assert alert.attempts == 1
    assert alert.sent_at is not None


def test_the_payload_is_the_documented_contract(incident, webhook):
    """Every key the catcher (and a future Slack integration) can rely on, pinned."""
    resolved_at = incident.opened_at + timedelta(seconds=74)
    services.resolve_incident(incident, now=resolved_at)
    alert = Alert.objects.get(incident=incident, kind=Alert.Kind.RESOLVED)

    tasks.send_alert(alert.pk)

    (request,) = webhook.requests
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "event": "incident.resolved",
        "monitor": incident.monitor.name,
        "monitor_id": incident.monitor.pk,
        "incident_id": incident.pk,
        "incident_kind": "availability",
        "cause": "test",
        "opened_at": incident.opened_at.isoformat(),
        "resolved_at": resolved_at.isoformat(),
        "downtime_seconds": 74,
    }


def test_running_send_alert_twice_sends_once(alert, webhook):
    tasks.send_alert(alert.pk)
    tasks.send_alert(alert.pk)

    assert len(webhook.requests) == 1
    alert.refresh_from_db()
    assert alert.attempts == 1


def test_send_alert_for_a_missing_row_does_nothing(webhook):
    tasks.send_alert(999_999)

    assert webhook.requests == []


# --- retries ----------------------------------------------------------------------


class Retried(Exception):
    """Stands in for celery.exceptions.Retry, which self.retry() raises."""


@pytest.fixture
def retries(monkeypatch) -> list[int]:
    """The countdowns send_alert.retry was asked for."""
    countdowns: list[int] = []

    def fake_retry(**kwargs):
        countdowns.append(kwargs["countdown"])
        raise Retried

    monkeypatch.setattr(tasks.send_alert, "retry", fake_retry)
    return countdowns


def test_a_failing_receiver_is_retried_with_exponential_backoff(
    alert, webhook, retries
):
    webhook.status = 503

    for _ in range(4):
        with pytest.raises(Retried):
            tasks.send_alert(alert.pk)

    assert retries == [2, 4, 8, 16]
    alert.refresh_from_db()
    assert alert.status == Alert.Status.PENDING
    assert alert.attempts == 4


def test_the_last_failed_attempt_marks_the_alert_failed_not_pending(
    alert, webhook, retries
):
    webhook.status = 503
    Alert.objects.filter(pk=alert.pk).update(
        attempts=settings.PULSE_ALERT_MAX_ATTEMPTS - 1
    )

    tasks.send_alert(alert.pk)  # fifth and last attempt: no retry raised

    assert retries == []
    alert.refresh_from_db()
    assert alert.status == Alert.Status.FAILED
    assert alert.attempts == settings.PULSE_ALERT_MAX_ATTEMPTS


@pytest.mark.parametrize("limit", [1, 2, 10])
def test_the_configured_limit_is_the_number_of_attempts(
    settings, alert, webhook, retries, limit
):
    settings.PULSE_ALERT_MAX_ATTEMPTS = limit
    webhook.status = 503

    for _ in range(limit - 1):
        with pytest.raises(Retried):
            tasks.send_alert(alert.pk)
    tasks.send_alert(alert.pk)

    alert.refresh_from_db()
    assert alert.status == Alert.Status.FAILED
    assert alert.attempts == limit
    assert len(webhook.requests) == limit
    assert len(retries) == limit - 1


def test_an_unreachable_receiver_counts_as_a_failed_attempt(
    alert, monkeypatch, retries
):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        alerting,
        "new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(refuse)),
    )

    with pytest.raises(Retried):
        tasks.send_alert(alert.pk)

    assert retries == [2]
