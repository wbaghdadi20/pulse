"""The log format: what one record looks like once the shared config has formatted it.

The formatter is built from the config without applying it: dictConfig would
replace the root logger's handlers, pytest's among them.
"""

import json
import logging
import logging.config
import sys
from typing import Any

import pytest
from celery.signals import setup_logging
from django.conf import settings

from core.logs import LEVELS, ContextFilter, logging_config, request_id


def json_formatter() -> logging.Formatter:
    config = logging_config("INFO")
    formatter: logging.Formatter = logging.config.DictConfigurator(
        config
    ).configure_formatter(config["formatters"]["json"])
    return formatter


def formatted(**fields: Any) -> str:
    record = logging.makeLogRecord(
        {"name": "monitors.tasks", "levelname": "INFO", "msg": "hello", **fields}
    )
    ContextFilter().filter(record)
    return json_formatter().format(record)


def test_a_record_is_one_json_object_with_time_level_logger_and_message():
    line = json.loads(formatted(msg="monitor %d ok=%s", args=(5, True)))

    assert line == {
        "time": line["time"],
        "level": "INFO",
        "logger": "monitors.tasks",
        "message": "monitor 5 ok=True",
    }
    assert line["time"].endswith("+00:00")


def test_extra_fields_become_keys():
    line = json.loads(formatted(monitor_id=5))

    assert line["monitor_id"] == 5


def test_a_traceback_stays_on_the_same_line():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        text = formatted(exc_info=sys.exc_info())

    assert "\n" not in text
    assert "RuntimeError: boom" in json.loads(text)["exc_info"]


def test_the_request_object_django_attaches_is_left_out():
    """Its text form carries the query string."""
    line = json.loads(formatted(request="<WSGIRequest: GET '/monitors/?after=5'>"))

    assert "request" not in line


def test_a_record_logged_during_a_request_carries_its_id():
    token = request_id.set("abc-123")
    try:
        line = json.loads(formatted())
    finally:
        request_id.reset(token)

    assert line["request_id"] == "abc-123"


def test_a_record_logged_outside_a_request_has_no_request_id_key():
    assert "request_id" not in json.loads(formatted())


def test_the_filter_is_on_the_handler_so_every_logger_passes_it():
    config = logging_config("INFO")

    assert config["handlers"]["stdout"]["filters"] == ["context"]
    assert config["root"]["handlers"] == ["stdout"]


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_name_is_accepted(level):
    assert logging_config(level)["root"]["level"] == level


@pytest.mark.parametrize("level", ["info", "VERBOSE", ""])
def test_an_unknown_level_fails_with_the_variable_name(level):
    with pytest.raises(ValueError, match="PULSE_LOG_LEVEL"):
        logging_config(level)


def test_django_is_configured_with_the_shared_config():
    assert settings.LOGGING == logging_config(settings.PULSE_LOG_LEVEL)


def test_celery_leaves_logging_to_that_config():
    """With no receiver on this signal, the worker installs its own text format."""
    assert setup_logging.receivers
