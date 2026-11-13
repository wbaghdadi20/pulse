"""Logging: one JSON object per line on stdout, the same in every process.

Why JSON on stdout, and what each process needed: docs/adr/0017-json-logs-and-request-ids.md
"""

import logging
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger.core import RESERVED_ATTRS

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# The id of the request being served. Set and cleared by core.middleware; None
# anywhere else. A ContextVar is per thread and per asyncio task, so two requests
# served at the same time each see their own.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class ContextFilter(logging.Filter):
    """Adds the current request's id to every record, whichever logger made it."""

    def filter(self, record: logging.LogRecord) -> bool:
        current = request_id.get()
        if current is not None:
            record.request_id = current
        return True


def logging_config(level: str) -> dict[str, Any]:
    """The dictConfig shared by Django (settings.LOGGING), the Celery worker and
    beat (config/celery.py) and gunicorn (gunicorn.conf.py)."""
    if level not in LEVELS:
        raise ValueError(f"PULSE_LOG_LEVEL={level!r} is not one of {', '.join(LEVELS)}")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"context": {"()": "core.logs.ContextFilter"}},
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
                # On the handler, not on a logger: a logger's filters are skipped
                # for records that reach it from a child logger.
                "filters": ["context"],
            },
            "null": {"class": "logging.NullHandler"},
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # Each of these arrives with a plain-text handler of its own. Naming
            # them here removes it; "django" then reaches the root handler, and
            # gunicorn's, which does not propagate, is given the handler directly.
            "django": {"handlers": [], "propagate": True},
            "gunicorn.error": {"handlers": ["stdout"], "propagate": False},
            # runserver's line per request. The middleware writes one with the id.
            "django.server": {"handlers": ["null"], "propagate": False},
            # At INFO httpx logs every request with its full URL. A monitor's URL
            # is its owner's, and may carry a key in the query string.
            "httpx": {"level": "WARNING"},
        },
    }
