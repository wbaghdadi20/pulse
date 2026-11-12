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
# Same ceiling as a monitor's timeout_s: a receiver slower than that is down.
MAX_WEBHOOK_TIMEOUT_S = 30
# A status page that can be five minutes behind an outage has stopped being one.
MAX_STATUS_CACHE_SECONDS = 300
# A token cannot be taken back, so its lifetime is how long a stolen one works.
MIN_JWT_TTL_SECONDS = 60
MAX_JWT_TTL_SECONDS = 86_400
# HS256 needs a key at least as long as its 256-bit output (RFC 7518, section 3.2).
MIN_JWT_SECRET_LENGTH = 32
# 100 requests a second from one client is no longer a limit.
MAX_RATE_LIMIT_PER_MINUTE = 6000
# One password guess a second: above that the limit has stopped slowing guessing down.
MAX_AUTH_RATE_LIMIT_PER_MINUTE = 60
# Each trusted proxy is one more X-Forwarded-For entry believed; none is the safe default.
MAX_NUM_PROXIES = 5


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
        "PULSE_WEBHOOK_TIMEOUT_S",
        1,
        MAX_WEBHOOK_TIMEOUT_S,
        id="pulse.E004",
        hint="Set it in .env. Every attempt can take this long, on top of the retry delays.",
    )
    errors += _in_range(
        "PULSE_STATUS_CACHE_SECONDS",
        0,
        MAX_STATUS_CACHE_SECONDS,
        id="pulse.E003",
        hint="Set it in .env. 0 turns the status page cache off.",
    )
    errors += _in_range(
        "PULSE_JWT_TTL_SECONDS",
        MIN_JWT_TTL_SECONDS,
        MAX_JWT_TTL_SECONDS,
        id="pulse.E005",
        hint="Set it in .env. A token cannot be revoked before it expires.",
    )
    if len(settings.PULSE_JWT_SECRET) < MIN_JWT_SECRET_LENGTH:
        errors.append(
            Error(
                f"PULSE_JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters.",
                hint='Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"',
                id="pulse.E006",
            )
        )
    errors += _in_range(
        "PULSE_RATE_LIMIT_PER_MINUTE",
        1,
        MAX_RATE_LIMIT_PER_MINUTE,
        id="pulse.E007",
        hint="Set it in .env. Requests per minute from one client address.",
    )
    errors += _in_range(
        "PULSE_AUTH_RATE_LIMIT_PER_MINUTE",
        1,
        MAX_AUTH_RATE_LIMIT_PER_MINUTE,
        id="pulse.E008",
        hint="Set it in .env. It is what slows down password guessing on /login.",
    )
    errors += _in_range(
        "PULSE_NUM_PROXIES",
        0,
        MAX_NUM_PROXIES,
        id="pulse.E009",
        hint="Set it in .env. Count only proxies you run: each is trusted to report the client's address.",
    )
    return errors


def _in_range(name: str, low: int, high: int, *, id: str, hint: str) -> list[Error]:
    value = getattr(settings, name)
    if low <= value <= high:
        return []
    return [
        Error(f"{name}={value} must be between {low} and {high}.", hint=hint, id=id)
    ]
