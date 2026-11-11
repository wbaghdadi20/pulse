"""The scheduled path: beat -> enqueue_due_checks -> run_check -> the "check now" service.

A Celery task object called like a function runs its body in-process, so the tasks
are called directly. run_check.delay is replaced with a recorder, so nothing is
queued; the check lock is real, on the test Redis database (see `redis_db`).
"""

from datetime import datetime, timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from config.celery import app
from monitors import tasks
from monitors.models import Check, Monitor
from tests.conftest import MONITOR_PAYLOAD

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("redis_db")]


def make_monitor(
    name: str, *, interval_s: int = 30, last_checked_at: datetime | None = None
) -> Monitor:
    fields = {**MONITOR_PAYLOAD, "name": name, "interval_s": interval_s}
    return Monitor.objects.create(last_checked_at=last_checked_at, **fields)


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    """The monitor ids run_check.delay was called with, instead of queueing them."""
    ids: list[int] = []
    monkeypatch.setattr(tasks.run_check, "delay", ids.append)
    return ids


def test_enqueue_queues_never_checked_and_overdue_monitors_only(queued):
    now = timezone.now()
    never = make_monitor("never")
    make_monitor("fresh", interval_s=30, last_checked_at=now - timedelta(seconds=5))
    overdue = make_monitor(
        "overdue", interval_s=30, last_checked_at=now - timedelta(seconds=31)
    )

    assert tasks.enqueue_due_checks() == 2
    assert sorted(queued) == sorted([never.pk, overdue.pk])


def test_enqueue_with_nothing_due_queues_nothing(queued):
    make_monitor("fresh", last_checked_at=timezone.now())

    assert tasks.enqueue_due_checks() == 0
    assert queued == []


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
    redis_db.set(f"lock:monitor:{monitor.pk}", "locked")

    tasks.run_check(monitor.pk)

    assert fake_check.calls == []
    assert not Check.objects.exists()


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


def test_a_checked_monitor_is_not_due_again_until_its_interval(
    monitor, fake_check, queued
):
    assert tasks.enqueue_due_checks() == 1
    tasks.run_check(monitor.pk)

    assert tasks.enqueue_due_checks() == 0


def test_beat_schedule_names_registered_tasks():
    """A typo here would only show up as "Received unregistered task" in the worker log."""
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks
