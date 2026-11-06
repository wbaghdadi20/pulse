"""Startup validation of Pulse settings, through Django's system check framework.

Checks run on `manage.py check`, `migrate` and `runserver`, so a bad value fails
fast with an error id instead of surfacing later as a monitor that never opens
an incident (a threshold larger than any streak of checks).
"""

from django.conf import settings
from django.core.checks import Error, register

# Route 53 and ALB cap their equivalents at 10; above that, detection time is
# better expressed by lengthening interval_s than by piling up checks.
MAX_THRESHOLD = 10


@register()
def check_incident_thresholds(app_configs, **kwargs):
    errors = []
    for name in ("PULSE_FAIL_THRESHOLD", "PULSE_OK_THRESHOLD"):
        value = getattr(settings, name)
        if not 1 <= value <= MAX_THRESHOLD:
            errors.append(
                Error(
                    f"{name}={value} must be between 1 and {MAX_THRESHOLD}.",
                    hint="Set it in .env; to detect slower, raise the monitor's interval_s instead.",
                    id="pulse.E001",
                )
            )
    return errors
