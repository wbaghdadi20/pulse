"""The scheduled path: beat -> enqueue_due_checks -> run_check -> the "check now" service.

A Celery task object called like a function runs its body in-process, so the tasks
are called directly. run_check.apply_async is replaced with a recorder, so nothing
is queued; the check lock is real, on the test Redis database (see `redis_db`).
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone

from config.celery import app
from monitors import tasks
from monitors.models import MAX_TIMEOUT_S, Check, Monitor
from tests.conftest import create_monitor

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("redis_db")]


def make_monitor(
    name: str, *, interval_s: int = 30, next_check_at: datetime | None = None
) -> Monitor:
    fields: dict[str, Any] = {"name": name, "interval_s": interval_s}
    if next_check_at is not None:
        fields["next_check_at"] = next_check_at
    return create_monitor(**fields)


@pytest.fixture
def queued(monkeypatch) -> list[dict[str, Any]]:
    """What run_check.apply_async was called with, instead of queueing it."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_check, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    return calls


def queued_ids(queued: list[dict[str, Any]]) -> list[int]:
    return sorted(call["args"][0] for call in queued)


def test_enqueue_queues_new_and_due_monitors_only(queued):
    now = timezone.now()
    new = make_monitor("new")
    make_monitor("later", next_check_at=now + timedelta(seconds=5))
    due = make_monitor("due", next_check_at=now - timedelta(seconds=1))

    assert tasks.enqueue_due_checks() == 2
    assert queued_ids(queued) == sorted([new.pk, due.pk])


def test_enqueue_with_nothing_due_queues_nothing(queued):
    make_monitor("later", next_check_at=timezone.now() + timedelta(seconds=5))

    assert tasks.enqueue_due_checks() == 0
    assert queued == []


def test_a_queued_check_expires_after_its_interval(queued):
    make_monitor("m", interval_s=45)

    tasks.enqueue_due_checks()

    assert [call["expires"] for call in queued] == [45]


def test_a_monitor_is_queued_once_per_interval_even_if_its_check_never_finishes(
    monitor, queued
):
    """The duplicate-check bug: "due" used to last until the check had finished."""
    assert tasks.enqueue_due_checks() == 1

    assert tasks.enqueue_due_checks() == 0
    assert tasks.enqueue_due_checks() == 0
    assert len(queued) == 1


def test_run_check_records_the_check_and_stamps_the_monitor(monitor, fake_check):
    tasks.run_check(monitor.pk)

    monitor.refresh_from_db()
    check = Check.objects.get(monitor=monitor)
    assert monitor.last_checked_at == check.checked_at


def test_run_check_for_a_deleted_monitor_does_nothing(fake_check):
    tasks.run_check(999_999)  # the row is gone, the message is stale: not an error

    assert not Check.objects.exists()


def test_run_check_skips_a_monitor_another_worker_is_checking(
    monitor, fake_check, redis_db
):
    key = f"lock:monitor:{monitor.pk}"
    redis_db.set(key, "the other worker's token")

    tasks.run_check(monitor.pk)

    assert fake_check.calls == []
    assert not Check.objects.exists()
    assert redis_db.get(key) == b"the other worker's token"


def test_run_check_frees_the_lock_when_it_finishes(monitor, fake_check, redis_db):
    tasks.run_check(monitor.pk)
    tasks.run_check(monitor.pk)

    assert len(fake_check.calls) == 2
    assert redis_db.keys("lock:*") == []


def test_run_check_frees_the_lock_when_the_check_blows_up(
    monitor, monkeypatch, redis_db
):
    def boom(monitor):
        raise RuntimeError("database gone")

    monkeypatch.setattr("monitors.services.check_monitor", boom)

    with pytest.raises(RuntimeError):
        tasks.run_check(monitor.pk)

    assert redis_db.keys("lock:*") == []


def test_run_check_leaves_a_lock_it_no_longer_owns(
    monitor, monkeypatch, redis_db, caplog
):
    key = f"lock:monitor:{monitor.pk}"

    def lock_expires_and_is_retaken(monitor_id):
        redis_db.set(key, "the next worker's token")

    monkeypatch.setattr(tasks, "_check", lock_expires_and_is_retaken)

    with caplog.at_level("WARNING"):
        tasks.run_check(monitor.pk)

    assert redis_db.get(key) == b"the next worker's token"
    assert "lock expired before the check finished" in caplog.text


def test_the_lock_outlives_the_longest_a_check_can_run():
    assert tasks.run_check.time_limit == tasks.CHECK_TIME_LIMIT_S
    assert tasks.CHECK_LOCK_TTL_S > tasks.CHECK_TIME_LIMIT_S > 2 * MAX_TIMEOUT_S


def test_beat_schedule_names_registered_tasks():
    """A typo here would only show up as "Received unregistered task" in the worker log."""
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks
