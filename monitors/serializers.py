from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from rest_framework import serializers


def validate_http_url(value: str) -> None:
    """Accept any http(s) URL with a hostname, including Docker service names."""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValidationError("URL must start with 'http://' or 'https://'.")
    if not parts.hostname:
        raise ValidationError("URL must include a hostname.")
    try:
        _ = parts.port
    except ValueError:
        raise ValidationError("URL has an invalid port.") from None


class MonitorSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=100)
    url = serializers.CharField(max_length=2048, validators=[validate_http_url])
    method = serializers.ChoiceField(choices=["GET", "HEAD"], default="GET")
    expected_status = serializers.IntegerField(
        default=200, min_value=100, max_value=599
    )
    timeout_s = serializers.IntegerField(min_value=1, max_value=30)
    interval_s = serializers.IntegerField(min_value=10)
