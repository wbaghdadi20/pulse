"""Logging: one JSON object per line on stdout, the same in every process.

Why JSON on stdout, and what each process needed: docs/adr/0017-json-logs-and-request-ids.md
"""

from typing import Any

from pythonjsonlogger.core import RESERVED_ATTRS

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def logging_config(level: str) -> dict[str, Any]:
    """The dictConfig shared by Django (settings.LOGGING), the Celery worker and
    beat (config/celery.py) and gunicorn (gunicorn.conf.py)."""
    if level not in LEVELS:
        raise ValueError(f"PULSE_LOG_LEVEL={level!r} is not one of {', '.join(LEVELS)}")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "fmt": ["levelname", "name", "message"],
                "rename_fields": {"levelname": "level", "name": "logger"},
                "timestamp": "time",
                # Django attaches the request object to its records. Its text form
                # includes the query string, which the logs otherwise never show.
                "reserved_attrs": [*RESERVED_ATTRS, "request"],
            }
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json",
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # Each of these arrives with a plain-text handler of its own. Naming
            # them here removes it; "django" then reaches the root handler, and
            # the three that do not propagate are given the handler directly.
            "django": {"handlers": [], "propagate": True},
            "django.server": {"handlers": ["stdout"], "propagate": False},
            "gunicorn.error": {"handlers": ["stdout"], "propagate": False},
            "gunicorn.access": {"handlers": ["stdout"], "propagate": False},
        },
    }
