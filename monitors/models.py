from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .validators import validate_http_url


class Monitor(models.Model):
    class Method(models.TextChoices):
        GET = "GET"
        HEAD = "HEAD"

    name = models.CharField(max_length=100)
    url = models.CharField(max_length=2048, validators=[validate_http_url])
    method = models.CharField(max_length=4, choices=Method.choices, default=Method.GET)
    expected_status = models.PositiveSmallIntegerField(
        default=200, validators=[MinValueValidator(100), MaxValueValidator(599)]
    )
    timeout_s = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    interval_s = models.PositiveIntegerField(validators=[MinValueValidator(10)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "monitors"
        constraints = [
            # Named, so the service can tell this violation apart from others.
            models.UniqueConstraint(fields=["name"], name="uq_monitor_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Check(models.Model):
    class ErrorType(models.TextChoices):
        TIMEOUT = "timeout"
        CONNECTION_REFUSED = "connection_refused"
        DNS = "dns"
        WRONG_STATUS = "wrong_status"
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
    monitor = models.ForeignKey(
        Monitor, on_delete=models.CASCADE, related_name="incidents"
    )
    opened_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)  # NULL while open
    cause = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "incidents"
        constraints = [
            # At most one open incident per monitor, enforced by Postgres:
            # a partial unique index over rows where resolved_at IS NULL.
            models.UniqueConstraint(
                fields=["monitor"],
                condition=Q(resolved_at__isnull=True),
                name="uq_one_open_incident",
            ),
        ]

    def __str__(self) -> str:
        state = "open" if self.resolved_at is None else "resolved"
        return f"Incident {self.pk} on {self.monitor_id} ({state})"
