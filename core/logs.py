"""Logging: one JSON object per line on stdout, the same in every process.

Why JSON on stdout, and what each process needed: docs/adr/0017-json-logs-and-request-ids.md
"""

import logging
from contextvars import ContextVar
from typing import Any

from celery import Task, current_task
from celery.signals import before_task_publish, task_postrun, task_prerun
from pythonjsonlogger.core import RESERVED_ATTRS

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# The id of the request being served. Set and cleared by core.middleware; None
# anywhere else. A ContextVar is per thread and per asyncio task, so two requests
# served at the same time each see their own.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class ContextFilter(logging.Filter):
    """Adds the current request's id and the running Celery task's id and name to
    every record, whichever logger made it.

    The request needs a variable of ours because Django keeps no "current request".
    Celery does keep a current task, so that one is read from Celery.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        current = request_id.get()
        if current is not None:
            record.request_id = current
        # A task called as a plain function is "current" too, but has no id.
        if current_task and current_task.request.id:
            record.task_id = current_task.request.id
            record.task = current_task.name
        return True


# A task queued while a request is being served takes the request's id with it, in
# the message headers, so the worker's lines can be found by the same id. A task
# that beat queued has none.


@before_task_publish.connect
def send_request_id(headers: dict[str, Any], **kwargs: Any) -> None:
    current = request_id.get()
    if current is not None:
        headers["request_id"] = current


@task_prerun.connect
def take_request_id(task: Task[Any, Any], **kwargs: Any) -> None:
    # A task run in place (apply) is still inside its caller's request.
    if not task.request.is_eager:
        request_id.set(getattr(task.request, "request_id", None))


@task_postrun.connect
def clear_request_id(task: Task[Any, Any], **kwargs: Any) -> None:
    if not task.request.is_eager:
        request_id.set(None)


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
        },
    }
