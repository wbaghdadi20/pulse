from datetime import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .validators import validate_http_url

MAX_TIMEOUT_S = 30


class Monitor(models.Model):
    class Method(models.TextChoices):
        GET = "GET"
        HEAD = "HEAD"

    # No single-column index: uq_monitor_owner_name below starts with owner_id.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monitors",
        db_index=False,
    )
    name = models.CharField(max_length=100)
    url = models.CharField(max_length=2048, validators=[validate_http_url])
    # Shown on the public status page. Off unless the owner turns it on.
    is_public = models.BooleanField(default=False)
    method = models.CharField(max_length=4, choices=Method.choices, default=Method.GET)
    expected_status = models.PositiveSmallIntegerField(
        default=200, validators=[MinValueValidator(100), MaxValueValidator(599)]
    )
    timeout_s = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_TIMEOUT_S)]
    )
    interval_s = models.PositiveIntegerField(validators=[MinValueValidator(10)])
    # Optional: {"<json key>": "<expected value>", ...}. When set, the check asks for
    # JSON, ignores the status code and judges the body by these keys only, so a
    # deep health check can be "up" while components we don't care about fail.
    assertions = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # The scheduler's cursor: due when this has passed. Moved one interval ahead
    # when the monitor is claimed, not when its check finishes.
    next_check_at = models.DateTimeField(default=timezone.now, db_index=True)
    # While set and in the future: checks run and are saved, but no incident opens
    # or resolves and no alert is sent (a planned deploy is not an outage).
    maintenance_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "monitors"
        constraints = [
            # Named, so the service can tell this violation apart from others.
            # Per owner: a name unique across everyone would tell one user which
            # names another user has taken.
            models.UniqueConstraint(
                fields=["owner", "name"], name="uq_monitor_owner_name"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def in_maintenance(self, now: datetime | None = None) -> bool:
        if self.maintenance_until is None:
            return False
        return self.maintenance_until > (now or timezone.now())


class Check(models.Model):
    class ErrorType(models.TextChoices):
        TIMEOUT = "timeout"
        CONNECTION_REFUSED = "connection_refused"
        DNS = "dns"
        WRONG_STATUS = "wrong_status"
        ASSERTION_FAILED = "assertion_failed"  # a JSON key missing or wrong
        BLOCKED = "blocked"  # the host resolved to an address checks may not call
        ERROR = "error"

    # No single-column index: the (monitor, checked_at) index below starts with
    # monitor_id, so it already covers lookups by monitor alone.
    monitor = models.ForeignKey(
        Monitor, on_delete=models.CASCADE, related_name="checks", db_index=False
    )
    checked_at = models.DateTimeField(default=timezone.now)
    ok = models.BooleanField()
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField()
    # NULL means "no error"; the API returns null, matching the D2 response.
    error_type = models.CharField(  # noqa: DJ001
        max_length=32, choices=ErrorType.choices, null=True, blank=True
    )

    class Meta:
        db_table = "checks"
        indexes = [
            # Matches the uptime query: WHERE monitor_id = ? AND checked_at > ?
            # monitor_id first (equality), checked_at second (range).
            models.Index(
                fields=["monitor", "checked_at"], name="checks_monitor_checked_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.monitor_id} @ {self.checked_at:%Y-%m-%d %H:%M:%S} ok={self.ok}"


class Incident(models.Model):
    class Kind(models.TextChoices):
        AVAILABILITY = "availability"

    monitor = models.ForeignKey(
        Monitor, on_delete=models.CASCADE, related_name="incidents"
    )
    kind = models.CharField(
        max_length=32, choices=Kind.choices, default=Kind.AVAILABILITY
    )
    opened_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)  # NULL while open
    cause = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "incidents"
        constraints = [
            # At most one open incident per (monitor, kind), enforced by Postgres:
            # a partial unique index over rows where resolved_at IS NULL. Two kinds
            # can be open on the same monitor at once; same kinds cannot.
            models.UniqueConstraint(
                fields=["monitor", "kind"],
                condition=Q(resolved_at__isnull=True),
                name="uq_one_open_incident_per_kind",
            ),
        ]

    def __str__(self) -> str:
        state = "open" if self.resolved_at is None else "resolved"
        return f"Incident {self.pk} on {self.monitor_id} ({self.kind}, {state})"


class Alert(models.Model):
    """One webhook per incident event. The unique constraint makes "send the opened
    alert" idempotent at the database: a second attempt to create it is a no-op."""

    class Kind(models.TextChoices):
        OPENED = "opened"
        RESOLVED = "resolved"

    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    incident = models.ForeignKey(
        Incident, on_delete=models.CASCADE, related_name="alerts"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "alerts"
        constraints = [
            models.UniqueConstraint(
                fields=["incident", "kind"], name="uq_alert_incident_kind"
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Alert {self.pk}: incident {self.incident_id} {self.kind} ({self.status})"
        )
