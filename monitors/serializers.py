from datetime import datetime
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from .models import Check, Incident, Monitor


class LatestCheckSerializer(serializers.ModelSerializer[Check]):
    class Meta:
        model = Check
        fields = ["checked_at", "ok", "status_code", "latency_ms", "error_type"]


class MonitorSerializer(serializers.ModelSerializer[Monitor]):
    """Fields, limits and the URL validator all come from the Monitor model."""

    latest_check = serializers.SerializerMethodField()

    class Meta:
        model = Monitor
        fields = [
            "id",
            "name",
            "url",
            "method",
            "expected_status",
            "timeout_s",
            "interval_s",
            "assertions",
            "created_at",
            "last_checked_at",
            "next_check_at",
            "maintenance_until",
            "latest_check",
        ]
        read_only_fields = ["id", "created_at", "last_checked_at", "next_check_at"]
        # ModelSerializer adds a UniqueValidator to `name` (from the model's
        # UniqueConstraint): a SELECT before the INSERT that answers 400 and still
        # races. Uniqueness is left to the database instead; the service turns a
        # violation into 409. max_length is kept: DRF adds it separately.
        extra_kwargs: dict[str, dict[str, Any]] = {"name": {"validators": []}}

    def get_latest_check(self, monitor: Monitor) -> dict[str, Any] | None:
        check = monitor.checks.order_by("-checked_at", "-id").first()
        return None if check is None else LatestCheckSerializer(check).data

    def validate_maintenance_until(self, value: datetime | None) -> datetime | None:
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError(
                "Must be in the future. Send null to end maintenance now."
            )
        return value

    def validate_assertions(self, value: object) -> dict[str, str] | None:
        """A JSONField accepts any JSON; we want a flat {key: expected string} object."""
        if value is None:
            return None
        if not isinstance(value, dict) or not value:
            raise serializers.ValidationError(
                'Must be a non-empty object: {"key": "expected value"}.'
            )
        if len(value) > 20:
            raise serializers.ValidationError("At most 20 keys.")
        for key, expected in value.items():
            if not isinstance(expected, str):
                raise serializers.ValidationError(
                    f'The expected value for "{key}" must be a string.'
                )
        return value


class IncidentSerializer(serializers.ModelSerializer[Incident]):
    class Meta:
        model = Incident
        fields = ["id", "monitor", "kind", "opened_at", "resolved_at", "cause"]
        read_only_fields = ["id", "monitor", "opened_at", "resolved_at"]
        # The "one open incident per kind" rule is enforced by the database, not by DRF.
        validators: list[Any] = []


class UptimeQuerySerializer(serializers.Serializer[Any]):
    """Validates the query string of GET /monitors/<id>/uptime/."""

    days = serializers.ChoiceField(choices=[7, 30, 90], default=7)
