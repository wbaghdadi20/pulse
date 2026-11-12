from urllib.parse import urlsplit

from django.core.exceptions import ValidationError


def validate_http_url(value: str) -> None:
    """Accept any http(s) URL with a hostname, including Docker service names.

    Django's URLValidator rejects hosts without a dot (e.g. "django") and accepts ftp://.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        # e.g. "http://[not-an-address]/": brackets that do not hold an IPv6 address.
        raise ValidationError("Enter a valid URL.") from None
    if parts.scheme not in ("http", "https"):
        raise ValidationError("URL must start with 'http://' or 'https://'.")
    if not parts.hostname:
        raise ValidationError("URL must include a hostname.")
    try:
        # Reading .port raises ValueError if the port isn't a valid number.
        _ = parts.port
    except ValueError:
        raise ValidationError("URL has an invalid port.") from None
