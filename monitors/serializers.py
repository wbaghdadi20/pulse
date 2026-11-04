from rest_framework import serializers

from .models import Monitor


class MonitorSerializer(serializers.ModelSerializer):
    """Fields, limits and the URL validator all come from the Monitor model."""

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
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        # ModelSerializer adds a UniqueValidator to `name` (from the model's
        # UniqueConstraint): a SELECT before the INSERT that answers 400 and still
        # races. Uniqueness is left to the database instead; the service turns a
        # violation into 409. max_length is kept: DRF adds it separately.
        extra_kwargs = {"name": {"validators": []}}


class UptimeQuerySerializer(serializers.Serializer):
    """Validates the query string of GET /monitors/<id>/uptime/."""

    days = serializers.ChoiceField(choices=[7, 30, 90], default=7)
