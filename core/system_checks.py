"""Startup validation of Pulse settings, through Django's system check framework.

Checks run on `manage.py check`, `migrate` and `runserver`, so a bad value fails
fast with an error id instead of surfacing later as a monitor that never opens
an incident, or an alert that is retried for hours.
"""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register

# Route 53 and ALB cap their equivalents at 10; above that, detection time is
# better expressed by lengthening interval_s than by piling up checks.
MAX_THRESHOLD = 10
# Ten doubling delays is ~17 minutes of retrying; an alert older than that is stale.
MAX_ALERT_ATTEMPTS = 10
# A status page that can be five minutes behind an outage has stopped being one.
MAX_STATUS_CACHE_SECONDS = 300


@register()
def check_bounded_settings(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[Error]:
    errors: list[Error] = []
    for name in ("PULSE_FAIL_THRESHOLD", "PULSE_OK_THRESHOLD"):
        errors += _in_range(
            name,
            1,
            MAX_THRESHOLD,
            id="pulse.E001",
            hint="Set it in .env; to detect slower, raise the monitor's interval_s instead.",
        )
    errors += _in_range(
        "PULSE_ALERT_MAX_ATTEMPTS",
        1,
        MAX_ALERT_ATTEMPTS,
        id="pulse.E002",
        hint="Set it in .env. Delays double per attempt, so 10 attempts already span ~17 minutes.",
    )
    errors += _in_range(
        "PULSE_STATUS_CACHE_SECONDS",
        0,
        MAX_STATUS_CACHE_SECONDS,
        id="pulse.E003",
        hint="Set it in .env. 0 turns the status page cache off.",
    )
    return errors


def _in_range(name: str, low: int, high: int, *, id: str, hint: str) -> list[Error]:
    value = getattr(settings, name)
    if low <= value <= high:
        return []
    return [
        Error(f"{name}={value} must be between {low} and {high}.", hint=hint, id=id)
    ]
