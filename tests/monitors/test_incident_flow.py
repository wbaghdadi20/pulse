"""The state machine wired to the database: checks in -> incidents out.

test_incidents.py proves decide() is right. These prove evaluate_incidents()
feeds it the right data and acts on the answer.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from monitors import services
from monitors.incidents import Action
from monitors.models import Alert, Check, Incident, Monitor
from tests.conftest import MONITOR_PAYLOAD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def thresholds(settings):
    # `settings` is pytest-django's fixture: changes are undone after each test.
    settings.PULSE_FAIL_THRESHOLD = 3
    settings.PULSE_OK_THRESHOLD = 2


def open_incidents(monitor):
    return Incident.objects.filter(monitor=monitor, resolved_at__isnull=True)


def test_three_failed_checks_open_one_incident(monitor, fake_check):
    fake_check(ok=False)

    services.check_monitor(monitor)
    services.check_monitor(monitor)
    assert open_incidents(monitor).count() == 0, "two failures are not enough"

    services.check_monitor(monitor)
    assert open_incidents(monitor).count() == 1

    services.check_monitor(monitor)  # a fourth failure
    assert open_incidents(monitor).count() == 1, "never opens twice"
    assert Incident.objects.filter(monitor=monitor).count() == 1


def test_incident_resolves_after_two_successes(monitor, fake_check):
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    incident = open_incidents(monitor).get()

    fake_check(ok=True)
    services.check_monitor(monitor)
    assert open_incidents(monitor).exists(), "one success is not enough"

    services.check_monitor(monitor)
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    assert not open_incidents(monitor).exists()


def test_a_blip_does_not_open_an_incident(monitor, fake_check):
    for ok in [True, False, True]:
        fake_check(ok=ok)
        services.check_monitor(monitor)
    assert not Incident.objects.filter(monitor=monitor).exists()


def test_evaluate_reports_the_action_it_took(monitor, fake_check):
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    # The third check_monitor already opened it; evaluating again must not re-open.
    assert services.evaluate_incidents(monitor) is Action.NOTHING

    fake_check(ok=True)
    services.check_monitor(monitor)
    services.check_monitor(monitor)
    assert services.evaluate_incidents(monitor) is Action.NOTHING  # already resolved


def test_an_opened_incident_says_why(monitor, fake_check):
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    assert open_incidents(monitor).get().cause == "3 failed checks in a row"


def test_incidents_are_per_monitor(monitor, fake_check):
    """An open incident on one monitor must not hide a new outage on another."""
    other = Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": "other"})
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    assert open_incidents(monitor).count() == 1

    for _ in range(3):
        services.check_monitor(other)
    assert open_incidents(other).count() == 1
    assert open_incidents(monitor).count() == 1


def test_checks_are_read_in_time_order_not_insertion_order(monitor, fake_check):
    """A late-arriving old success (e.g. a backfill) must not mask three fresh failures."""
    fake_check(ok=False)
    services.check_monitor(monitor)
    services.check_monitor(monitor)
    Check.objects.create(  # older than the failures, but inserted after them
        monitor=monitor,
        checked_at=timezone.now() - timedelta(hours=1),
        ok=True,
        status_code=200,
        latency_ms=1,
    )

    services.check_monitor(monitor)  # third failure in a row by time
    assert open_incidents(monitor).count() == 1


def test_thresholds_come_from_settings(monitor, fake_check, settings):
    settings.PULSE_FAIL_THRESHOLD = 1
    fake_check(ok=False)
    services.check_monitor(monitor)
    assert open_incidents(monitor).count() == 1


# --- maintenance windows -----------------------------------------------------------


def in_maintenance(monitor, *, for_seconds=3600):
    monitor.maintenance_until = timezone.now() + timedelta(seconds=for_seconds)
    monitor.save(update_fields=["maintenance_until"])


def test_in_maintenance_checks_are_saved_but_no_incident_opens(monitor, fake_check):
    in_maintenance(monitor)
    fake_check(ok=False)

    for _ in range(4):
        assert services.check_monitor(monitor)["ok"] is False

    assert Check.objects.filter(monitor=monitor).count() == 4
    assert not Incident.objects.filter(monitor=monitor).exists()
    assert not Alert.objects.exists()


def test_in_maintenance_an_open_incident_is_left_alone(monitor, fake_check):
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    incident = open_incidents(monitor).get()
    in_maintenance(monitor)

    fake_check(ok=True)
    for _ in range(3):
        services.check_monitor(monitor)

    incident.refresh_from_db()
    assert incident.resolved_at is None
    assert not Alert.objects.filter(
        incident=incident, kind=Alert.Kind.RESOLVED
    ).exists()


def test_when_maintenance_ends_the_next_check_judges_normally(monitor, fake_check):
    in_maintenance(monitor)
    fake_check(ok=False)
    for _ in range(3):
        services.check_monitor(monitor)
    assert not open_incidents(monitor).exists()

    in_maintenance(monitor, for_seconds=-1)  # the window is over
    services.check_monitor(monitor)

    assert open_incidents(monitor).count() == 1, (
        "still down after the window: a real incident"
    )


def test_evaluate_reports_nothing_during_maintenance(monitor, fake_check):
    in_maintenance(monitor)
    fake_check(ok=False)
    for _ in range(3):
        assert services.check_monitor(monitor) is not None
    assert services.evaluate_incidents(monitor) is Action.NOTHING
