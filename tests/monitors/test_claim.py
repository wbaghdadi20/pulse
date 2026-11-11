"""Claiming due monitors: each is handed out once per interval, to one caller."""

import threading
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from monitors import services
from monitors.models import Monitor
from tests.conftest import MONITOR_PAYLOAD


def make_monitor(name: str, **fields) -> Monitor:
    return Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": name, **fields})


@pytest.mark.django_db
def test_a_new_monitor_is_due_straight_away(monitor):
    assert services.claim_due_monitors() == [(monitor.pk, monitor.interval_s)]


@pytest.mark.django_db
def test_claiming_moves_the_monitor_one_interval_ahead(monitor):
    now = timezone.now()

    services.claim_due_monitors(now=now)

    monitor.refresh_from_db()
    assert monitor.next_check_at == now + timedelta(seconds=monitor.interval_s)


@pytest.mark.django_db
def test_a_claimed_monitor_is_due_again_exactly_when_its_slot_arrives(monitor):
    now = timezone.now()
    services.claim_due_monitors(now=now)
    slot = now + timedelta(seconds=monitor.interval_s)

    assert services.claim_due_monitors(now=slot - timedelta(milliseconds=1)) == []
    assert services.claim_due_monitors(now=slot) == [(monitor.pk, monitor.interval_s)]


@pytest.mark.django_db
def test_each_monitor_advances_by_its_own_interval():
    fast = make_monitor("fast", interval_s=10)
    slow = make_monitor("slow", interval_s=300)
    now = timezone.now()

    services.claim_due_monitors(now=now)

    fast.refresh_from_db()
    slow.refresh_from_db()
    assert fast.next_check_at == now + timedelta(seconds=10)
    assert slow.next_check_at == now + timedelta(seconds=300)


def claim_on_another_connection() -> list[tuple[int, int]] | None:
    """What a second worker would get right now. None if it had to wait."""
    result: list[list[tuple[int, int]]] = []

    def work() -> None:
        try:
            result.append(services.claim_due_monitors())
        finally:
            connection.close()

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(timeout=3)
    return result[0] if result else None


@pytest.mark.django_db(transaction=True)
def test_a_monitor_another_worker_is_claiming_is_skipped_not_waited_for():
    """Two workers run enqueue_due_checks at the same moment."""
    monitor = make_monitor("contended")

    with transaction.atomic():
        first = services.claim_due_monitors()  # this worker, not yet committed

        second = claim_on_another_connection()

    assert first == [(monitor.pk, monitor.interval_s)]
    assert second == [], "skipped the locked row and returned without waiting"
    assert claim_on_another_connection() == [], "committed: now it is simply not due"
