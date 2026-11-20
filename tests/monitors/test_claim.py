"""Claiming due monitors: each is handed out once per interval, to one caller."""

import threading
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.db import connection, transaction

from monitors import services
from monitors.models import Monitor
from orgs.models import Organization
from tests.conftest import create_monitor


def make_monitor(name: str, **fields) -> Monitor:
    return create_monitor(name=name, **fields)


@pytest.mark.django_db
def test_a_new_monitor_is_due_straight_away(monitor):
    assert services.claim_due_monitors() == [(monitor.pk, monitor.interval_s)]


@pytest.mark.django_db
def test_claiming_moves_the_monitor_to_its_next_slot(monitor):
    slot = monitor.next_check_at
    a_moment_later = slot + timedelta(milliseconds=40)

    services.claim_due_monitors(now=a_moment_later)

    monitor.refresh_from_db()
    assert monitor.next_check_at == slot + timedelta(seconds=monitor.interval_s), (
        "from the slot, not from when the scheduler got round to it"
    )


@pytest.mark.django_db
def test_a_claimed_monitor_is_due_again_exactly_when_its_slot_arrives(monitor):
    slot = monitor.next_check_at
    services.claim_due_monitors(now=slot)
    following = slot + timedelta(seconds=monitor.interval_s)

    assert services.claim_due_monitors(now=following - timedelta(milliseconds=1)) == []
    assert services.claim_due_monitors(now=following) == [
        (monitor.pk, monitor.interval_s)
    ]


@pytest.mark.django_db
def test_a_scheduler_that_runs_a_little_late_does_not_skip_the_next_tick():
    """Ticks every 10 s for a 10 s monitor, each a few ms late: due at every one."""
    monitor = make_monitor("every tick", interval_s=10)
    first_tick = monitor.next_check_at
    lateness_ms = [30, 5, 45, 1, 20]

    claimed = [
        services.claim_due_monitors(
            now=first_tick + timedelta(seconds=10 * tick, milliseconds=late)
        )
        for tick, late in enumerate(lateness_ms)
    ]

    assert claimed == [[(monitor.pk, 10)]] * len(lateness_ms)


@pytest.mark.django_db
def test_after_missed_slots_the_schedule_restarts_from_now_without_catching_up():
    monitor = make_monitor("slow", interval_s=300)
    back_after_an_hour = monitor.next_check_at + timedelta(hours=1)

    assert len(services.claim_due_monitors(now=back_after_an_hour)) == 1

    ten_seconds_on = back_after_an_hour + timedelta(seconds=10)
    assert services.claim_due_monitors(now=ten_seconds_on) == [], "no catching up"
    monitor.refresh_from_db()
    assert monitor.next_check_at == back_after_an_hour + timedelta(seconds=300)


@pytest.mark.django_db
def test_each_monitor_advances_by_its_own_interval():
    fast = make_monitor("fast", interval_s=10)
    slow = make_monitor("slow", interval_s=300)
    now = max(fast.next_check_at, slow.next_check_at)

    services.claim_due_monitors(now=now)

    before = {fast.pk: fast.next_check_at, slow.pk: slow.next_check_at}
    fast.refresh_from_db()
    slow.refresh_from_db()
    assert fast.next_check_at == before[fast.pk] + timedelta(seconds=10)
    assert slow.next_check_at == before[slow.pk] + timedelta(seconds=300)


def on_another_connection[T](org: Organization, work: Callable[[], T]) -> T | None:
    """What `work` returns in a thread, on its own connection, inside `org`."""
    result: list[T] = []

    def run() -> None:
        try:
            # A new connection starts in public: the thread enters the tenant itself.
            with org:
                result.append(work())
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=3)
    return result[0] if result else None


def claim_on_another_connection(org: Organization) -> list[tuple[int, int]] | None:
    return on_another_connection(org, services.claim_due_monitors)


def test_what_a_committed_test_writes_another_connection_sees(committed):
    monitor = make_monitor("visible", assignee=None)

    assert on_another_connection(
        committed, lambda: Monitor.objects.filter(pk=monitor.pk).exists()
    )


def test_a_monitor_another_worker_is_claiming_is_skipped_not_waited_for(committed):
    """Two workers run enqueue_due_checks at the same moment."""
    monitor = make_monitor("contended", assignee=None)
    assert on_another_connection(
        committed, lambda: Monitor.objects.filter(pk=monitor.pk).exists()
    ), "the other worker must see the monitor, or skipping it proves nothing"

    with transaction.atomic():
        first = services.claim_due_monitors()  # this worker, not yet committed

        second = claim_on_another_connection(committed)

    assert first == [(monitor.pk, monitor.interval_s)]
    assert second == [], "skipped the locked row and returned without waiting"
    assert claim_on_another_connection(committed) == [], (
        "committed: now it is simply not due"
    )
