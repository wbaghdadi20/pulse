"""The state machine wired to the database: checks in -> incidents out.

test_incidents.py proves decide() is right. These prove evaluate_incidents()
feeds it the right data and acts on the answer.
"""

import pytest

from monitors import services
from monitors.incidents import Action
from monitors.models import Incident

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


def test_thresholds_come_from_settings(monitor, fake_check, settings):
    settings.PULSE_FAIL_THRESHOLD = 1
    fake_check(ok=False)
    services.check_monitor(monitor)
    assert open_incidents(monitor).count() == 1
