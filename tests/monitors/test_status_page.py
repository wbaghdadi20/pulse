"""GET /status: what the public page says, and how many queries it takes to say it."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from monitors import services, status_page, tasks
from monitors.models import Check, Incident, Monitor
from tests.conftest import create_monitor

pytestmark = pytest.mark.django_db


def make_monitor(name: str) -> Monitor:
    return create_monitor(name=name)


def add_check(monitor: Monitor, *, ok: bool, latency_ms: int, age: timedelta) -> None:
    Check.objects.create(
        monitor=monitor,
        ok=ok,
        status_code=200 if ok else 500,
        latency_ms=latency_ms,
        checked_at=timezone.now() - age,
    )


def page_entry(api, name: str) -> dict[str, Any]:
    response = api.get("/status")
    assert response.status_code == 200
    entries: list[dict[str, Any]] = response.json()["monitors"]
    (entry,) = [m for m in entries if m["name"] == name]
    return entry


def test_a_monitor_with_no_open_incident_is_up(api, monitor):
    assert page_entry(api, monitor.name)["state"] == "up"


def test_a_monitor_with_an_open_incident_is_down(api, monitor):
    Incident.objects.create(monitor=monitor, cause="3 failed checks in a row")

    assert page_entry(api, monitor.name)["state"] == "down"


def test_a_resolved_incident_does_not_keep_a_monitor_down(api, monitor):
    Incident.objects.create(monitor=monitor, resolved_at=timezone.now())

    assert page_entry(api, monitor.name)["state"] == "up"


def test_uptime_and_p95_cover_the_last_90_days(api, monitor):
    add_check(monitor, ok=True, latency_ms=100, age=timedelta(days=1))
    add_check(monitor, ok=True, latency_ms=200, age=timedelta(days=89))
    add_check(monitor, ok=False, latency_ms=300, age=timedelta(days=30))
    add_check(monitor, ok=False, latency_ms=9000, age=timedelta(days=91))  # too old

    entry = page_entry(api, monitor.name)

    assert entry["uptime_percent"] == 66.67
    assert entry["p95_latency_ms"] == 290


def test_a_monitor_never_checked_has_no_numbers(api, monitor):
    entry = page_entry(api, monitor.name)

    assert entry["uptime_percent"] is None
    assert entry["p95_latency_ms"] is None
    assert entry["incidents"] == []


def test_numbers_belong_to_their_own_monitor(api, monitor):
    other = make_monitor("other")
    add_check(monitor, ok=True, latency_ms=10, age=timedelta(hours=1))
    add_check(other, ok=False, latency_ms=999, age=timedelta(hours=1))
    Incident.objects.create(monitor=other, cause="theirs")

    mine, theirs = page_entry(api, monitor.name), page_entry(api, "other")

    assert (mine["uptime_percent"], mine["state"], mine["incidents"]) == (100, "up", [])
    assert (theirs["uptime_percent"], theirs["state"]) == (0, "down")
    assert [i["cause"] for i in theirs["incidents"]] == ["theirs"]


def test_only_the_latest_incidents_are_listed_newest_first(api, monitor):
    now = timezone.now()
    for days_ago in [5, 4, 3, 2]:
        opened = now - timedelta(days=days_ago)
        Incident.objects.create(
            monitor=monitor,
            opened_at=opened,
            resolved_at=opened + timedelta(minutes=5),
            cause=f"{days_ago} days ago",
        )
    Incident.objects.create(monitor=monitor, opened_at=now, cause="now")

    incidents = page_entry(api, monitor.name)["incidents"]

    assert [i["cause"] for i in incidents] == ["now", "2 days ago", "3 days ago"]
    assert incidents[0]["resolved_at"] is None
    assert set(incidents[0]) == {"id", "kind", "cause", "opened_at", "resolved_at"}


def test_monitors_are_listed_by_name(api, db):
    for name in ["shop", "api", "flaky"]:
        make_monitor(name)

    names = [m["name"] for m in api.get("/status").json()["monitors"]]

    assert names == ["api", "flaky", "shop"]


def test_the_page_does_not_leak_where_a_monitor_points(api, monitor):
    entry = page_entry(api, monitor.name)

    assert set(entry) == {
        "id",
        "name",
        "state",
        "uptime_percent",
        "p95_latency_ms",
        "incidents",
    }


def test_the_page_is_public(api, monitor):
    api.credentials()  # no Authorization header at all

    assert api.get("/status").status_code == 200


@pytest.mark.parametrize("monitors", [1, 8])
def test_building_the_page_takes_four_queries_whatever_the_number_of_monitors(
    db, django_assert_num_queries, monitors
):
    for n in range(monitors):
        m = make_monitor(f"m{n}")
        add_check(m, ok=True, latency_ms=10, age=timedelta(hours=1))
        Incident.objects.create(monitor=m, cause="x")

    with django_assert_num_queries(4):
        page = status_page.build_status_page()

    assert len(page["monitors"]) == monitors


# --- the cache ----------------------------------------------------------------------


def test_a_second_request_is_served_from_the_cache(
    api, monitor, django_assert_num_queries
):
    first = api.get("/status").json()

    with django_assert_num_queries(0):
        second = api.get("/status").json()

    assert second == first


class EmptyCache:
    """Always a miss; remembers what it was asked to keep and for how long."""

    def __init__(self) -> None:
        self.kept: list[tuple[str, int]] = []

    def get(self, key: str) -> None:
        return None

    def set(self, key: str, value: object, timeout: int) -> None:
        self.kept.append((key, timeout))


def test_the_page_is_kept_for_the_configured_seconds(
    api, monitor, settings, monkeypatch
):
    settings.PULSE_STATUS_CACHE_SECONDS = 7
    cache = EmptyCache()
    monkeypatch.setattr(status_page, "cache", cache)

    api.get("/status")

    assert cache.kept == [("status:page", 7)]


def test_zero_seconds_turns_the_cache_off(
    api, monitor, settings, django_assert_num_queries
):
    settings.PULSE_STATUS_CACHE_SECONDS = 0
    api.get("/status")

    with django_assert_num_queries(4):
        api.get("/status")


# --- no stale page ------------------------------------------------------------------


@pytest.fixture
def no_alerts(monkeypatch) -> None:
    """Committing an incident queues its alert; these tests are not about that."""
    monkeypatch.setattr(tasks.send_alert, "delay", lambda alert_id: None)


def test_an_incident_that_opens_shows_on_the_next_request(
    api, monitor, no_alerts, django_capture_on_commit_callbacks
):
    assert page_entry(api, monitor.name)["state"] == "up"  # and now it is cached

    with django_capture_on_commit_callbacks(execute=True):
        services.open_incident_for(monitor, cause="3 failed checks in a row")

    entry = page_entry(api, monitor.name)
    assert entry["state"] == "down"
    assert [i["cause"] for i in entry["incidents"]] == ["3 failed checks in a row"]


def test_an_incident_that_resolves_shows_on_the_next_request(
    api, monitor, no_alerts, django_capture_on_commit_callbacks
):
    incident = Incident.objects.create(monitor=monitor)
    assert page_entry(api, monitor.name)["state"] == "down"

    with django_capture_on_commit_callbacks(execute=True):
        services.resolve_incident(incident)

    entry = page_entry(api, monitor.name)
    assert entry["state"] == "up"
    assert entry["incidents"][0]["resolved_at"] is not None


def test_the_cached_page_is_dropped_at_commit_not_before(
    api, monitor, django_capture_on_commit_callbacks
):
    cached = api.get("/status").json()

    with django_capture_on_commit_callbacks(execute=False):
        services.open_incident_for(monitor)

        assert api.get("/status").json() == cached
