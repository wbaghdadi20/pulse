"""In the worker: the task's id on every line, and the id of the request that queued it."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from celery import shared_task
from celery.signals import before_task_publish, task_postrun, task_prerun

from core import logs
from core.logs import request_id
from tests.core.test_logs import formatted

lines: list[dict[str, Any]] = []


@shared_task(name="tests.log_a_line")
def log_a_line() -> None:
    lines.append(json.loads(formatted()))


@pytest.fixture(autouse=True)
def no_lines_yet() -> None:
    lines.clear()


def in_worker(**request: Any) -> Any:
    """A task as the worker runs it: not in place, with these message headers."""
    return SimpleNamespace(request=SimpleNamespace(is_eager=False, **request))


# --- the task id --------------------------------------------------------------------


def test_a_line_logged_inside_a_task_carries_its_id_and_name():
    log_a_line.apply(task_id="task-1")

    assert lines[0]["task_id"] == "task-1"
    assert lines[0]["task"] == "tests.log_a_line"


def test_a_task_called_as_a_plain_function_has_no_task_id():
    log_a_line()

    assert "task_id" not in lines[0]


def test_a_line_logged_outside_a_task_has_no_task_id():
    assert "task_id" not in json.loads(formatted())


# --- the request id, from the API to the worker -------------------------------------


def test_a_task_queued_during_a_request_takes_the_id_in_its_headers():
    headers: dict[str, Any] = {}
    token = request_id.set("abc-123")
    try:
        logs.send_request_id(headers=headers)
    finally:
        request_id.reset(token)

    assert headers == {"request_id": "abc-123"}


def test_a_task_queued_outside_a_request_gets_no_header():
    headers: dict[str, Any] = {}

    logs.send_request_id(headers=headers)

    assert headers == {}


def test_the_worker_logs_under_that_id_while_the_task_runs_and_not_after():
    task = in_worker(request_id="abc-123")

    logs.take_request_id(task=task)
    during = request_id.get()
    logs.clear_request_id(task=task)

    assert during == "abc-123"
    assert request_id.get() is None


def test_a_task_that_beat_queued_has_no_request_id():
    logs.take_request_id(task=in_worker())

    assert request_id.get() is None


def test_a_task_run_in_place_stays_in_its_callers_request():
    token = request_id.set("abc-123")
    try:
        log_a_line.apply(task_id="task-1")
        after = request_id.get()
    finally:
        request_id.reset(token)

    assert lines[0]["request_id"] == "abc-123"
    assert after == "abc-123"


@pytest.mark.parametrize("signal", [before_task_publish, task_prerun, task_postrun])
def test_the_receivers_are_connected(signal):
    assert signal.receivers
