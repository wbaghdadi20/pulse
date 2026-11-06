"""API tests against the real (test) database.

Every test here hits Postgres through the full stack: URL routing, DRF view,
serializer, service, ORM. Slower than the pure tests, but the only way to test
what the database enforces (unique names, one open incident).
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from monitors.models import Check, Incident, Monitor
from tests.conftest import MONITOR_PAYLOAD

pytestmark = pytest.mark.django_db  # every test in this file may use the database


# --- create --------------------------------------------------------------------


def test_create_returns_201_and_the_monitor(api):
    response = api.post(reverse("monitor-list"), MONITOR_PAYLOAD, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "backend"
    assert body["method"] == "GET"  # default filled in
    assert body["expected_status"] == 200
    assert Monitor.objects.filter(pk=body["id"]).exists()


def test_create_with_invalid_input_returns_400_per_field(api):
    bad = {**MONITOR_PAYLOAD, "url": "ftp://x", "timeout_s": 99, "interval_s": 1}
    response = api.post(reverse("monitor-list"), bad, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {"url", "timeout_s", "interval_s"}


def test_create_accepts_docker_hostnames(api):
    payload = {**MONITOR_PAYLOAD, "url": "http://django:8000/ht/"}
    assert api.post(reverse("monitor-list"), payload, format="json").status_code == 201


def test_duplicate_name_returns_409(api, monitor):
    response = api.post(reverse("monitor-list"), MONITOR_PAYLOAD, format="json")

    assert response.status_code == 409
    assert response.json() == {"detail": 'A monitor named "backend" already exists.'}
    assert Monitor.objects.count() == 1


# --- read ----------------------------------------------------------------------


def test_get_one(api, monitor):
    response = api.get(reverse("monitor-detail", args=[monitor.pk]))
    assert response.status_code == 200
    assert response.json()["id"] == monitor.pk


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_unknown_monitor_returns_404(api, method):
    response = getattr(api, method)(reverse("monitor-detail", args=[999]))
    assert response.status_code == 404
    assert response.json() == {"detail": "Monitor 999 not found."}


def test_non_numeric_id_is_404(api):
    assert api.get("/monitors/abc/").status_code == 404


# --- update / delete -----------------------------------------------------------


def test_patch_changes_only_the_sent_fields(api, monitor):
    response = api.patch(
        reverse("monitor-detail", args=[monitor.pk]), {"timeout_s": 9}, format="json"
    )

    assert response.status_code == 200
    monitor.refresh_from_db()
    assert monitor.timeout_s == 9
    assert monitor.name == "backend"  # untouched


def test_patch_to_a_taken_name_returns_409(api, monitor):
    other = Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": "other"})
    response = api.patch(
        reverse("monitor-detail", args=[other.pk]), {"name": "backend"}, format="json"
    )
    assert response.status_code == 409


def test_patch_to_own_name_is_fine(api, monitor):
    response = api.patch(
        reverse("monitor-detail", args=[monitor.pk]), {"name": "backend"}, format="json"
    )
    assert response.status_code == 200


def test_delete_returns_204_then_404(api, monitor):
    url = reverse("monitor-detail", args=[monitor.pk])
    assert api.delete(url).status_code == 204
    assert api.delete(url).status_code == 404
    assert not Monitor.objects.filter(pk=monitor.pk).exists()


# --- pagination ----------------------------------------------------------------


def test_list_is_paginated_with_a_total(api, db):
    for i in range(25):
        Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": f"m{i:02d}"})

    page1 = api.get(reverse("monitor-list")).json()
    assert page1["count"] == 25
    assert len(page1["results"]) == 20  # default limit
    assert "offset=20" in page1["next"]
    assert page1["previous"] is None

    page2 = api.get(reverse("monitor-list"), {"limit": 5, "offset": 20}).json()
    assert [m["name"] for m in page2["results"]] == ["m20", "m21", "m22", "m23", "m24"]
    assert page2["next"] is None


def test_list_limit_is_capped(api, db):
    for i in range(3):
        Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": f"m{i}"})
    response = api.get(reverse("monitor-list"), {"limit": 100000})
    assert len(response.json()["results"]) == 3  # fine: cap is 100, we have 3


# --- check now -----------------------------------------------------------------


def test_check_now_records_a_check_and_returns_it(api, monitor, fake_check):
    fake_check(ok=False, error_type="connection_refused")

    response = api.post(reverse("monitor-check", args=[monitor.pk]))

    assert response.status_code == 200
    assert response.json()["error_type"] == "connection_refused"
    check = Check.objects.get(monitor=monitor)
    assert check.ok is False
    assert check.error_type == "connection_refused"


def test_check_now_on_unknown_monitor_is_404(api, fake_check):
    assert api.post(reverse("monitor-check", args=[999])).status_code == 404


def test_check_now_checks_that_monitor_and_no_other(api, monitor, fake_check):
    other = Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": "other"})

    api.post(reverse("monitor-check", args=[other.pk]))

    assert fake_check.calls == [other]
    assert Check.objects.filter(monitor=other).count() == 1
    assert not Check.objects.filter(monitor=monitor).exists()


# --- uptime (with a pinned clock) ----------------------------------------------


def test_uptime_counts_only_the_requested_window(api, monitor, settings):
    now = timezone.now()
    for days_ago, ok, latency in [
        (1, True, 100),
        (2, True, 200),
        (3, False, 300),  # inside 7 days
        (10, False, 999),  # outside 7 days, inside 30
    ]:
        Check.objects.create(
            monitor=monitor,
            checked_at=now - timedelta(days=days_ago),
            ok=ok,
            status_code=200 if ok else 503,
            latency_ms=latency,
            error_type=None if ok else "wrong_status",
        )

    week = api.get(reverse("monitor-uptime", args=[monitor.pk]), {"days": 7}).json()
    assert week == {
        "days": 7,
        "uptime_percent": 66.67,  # 2 of 3
        "p95_latency_ms": 290,  # percentile_cont over 100, 200, 300
        "checks": 3,
    }

    month = api.get(reverse("monitor-uptime", args=[monitor.pk]), {"days": 30}).json()
    assert month["checks"] == 4
    assert month["uptime_percent"] == 50.0


def test_uptime_counts_only_this_monitors_checks(api, monitor):
    other = Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": "other"})
    now = timezone.now()
    for owner, ok in [(monitor, True), (other, False), (other, False)]:
        Check.objects.create(
            monitor=owner, checked_at=now, ok=ok, status_code=200, latency_ms=1
        )

    body = api.get(reverse("monitor-uptime", args=[monitor.pk])).json()
    assert body["checks"] == 1
    assert body["uptime_percent"] == 100.0


def test_uptime_with_no_checks_returns_nulls(api, monitor):
    body = api.get(reverse("monitor-uptime", args=[monitor.pk])).json()
    assert body == {
        "days": 7,
        "uptime_percent": None,
        "p95_latency_ms": None,
        "checks": 0,
    }


@pytest.mark.parametrize("days", ["5", "abc"])
def test_uptime_rejects_other_windows(api, monitor, days):
    response = api.get(reverse("monitor-uptime", args=[monitor.pk]), {"days": days})
    assert response.status_code == 400
    assert "days" in response.json()


# --- incidents -----------------------------------------------------------------


def test_second_open_incident_is_409_never_500(api, monitor):
    url = reverse("monitor-incidents", args=[monitor.pk])
    assert api.post(url, {"cause": "manual"}, format="json").status_code == 201
    response = api.post(url, {}, format="json")

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Monitor {monitor.pk} already has an open incident."
    }
    assert (
        Incident.objects.filter(monitor=monitor, resolved_at__isnull=True).count() == 1
    )


def test_incident_can_reopen_after_being_resolved(api, monitor):
    url = reverse("monitor-incidents", args=[monitor.pk])
    first = api.post(url, {}, format="json").json()
    Incident.objects.filter(pk=first["id"]).update(resolved_at=timezone.now())

    assert api.post(url, {}, format="json").status_code == 201
    assert Incident.objects.filter(monitor=monitor).count() == 2
