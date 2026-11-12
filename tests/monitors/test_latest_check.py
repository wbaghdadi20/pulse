"""Every monitor in the API carries its latest check, without a query per monitor."""

from datetime import timedelta

import pytest
from django.utils import timezone

from monitors.models import Check, Monitor
from tests.conftest import MONITOR_PAYLOAD

pytestmark = pytest.mark.django_db


def add_check(monitor: Monitor, *, ok: bool = True, minutes_ago: int = 0) -> Check:
    return Check.objects.create(
        monitor=monitor,
        ok=ok,
        status_code=200 if ok else 503,
        latency_ms=12,
        error_type=None if ok else "wrong_status",
        checked_at=timezone.now() - timedelta(minutes=minutes_ago),
    )


def make_monitors(count: int) -> list[Monitor]:
    monitors = [
        Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": f"m{n}"})
        for n in range(count)
    ]
    for monitor in monitors:
        add_check(monitor, minutes_ago=5)
        add_check(monitor, ok=False)
    return monitors


def test_the_list_shows_each_monitors_latest_check(api, monitor):
    add_check(monitor, ok=True, minutes_ago=10)
    newest = add_check(monitor, ok=False, minutes_ago=1)
    add_check(monitor, ok=True, minutes_ago=5)  # inserted last, but older

    (row,) = api.get("/monitors/").json()["results"]

    assert row["latest_check"] == {
        "checked_at": newest.checked_at.isoformat().replace("+00:00", "Z"),
        "ok": False,
        "status_code": 503,
        "latency_ms": 12,
        "error_type": "wrong_status",
    }


def test_a_monitor_never_checked_has_no_latest_check(api, monitor):
    (row,) = api.get("/monitors/").json()["results"]

    assert row["latest_check"] is None


def test_latest_checks_are_not_mixed_up_between_monitors(api, monitor):
    other = Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": "other"})
    add_check(monitor, ok=True)
    add_check(other, ok=False)

    rows = {row["name"]: row for row in api.get("/monitors/").json()["results"]}

    assert rows[monitor.name]["latest_check"]["ok"] is True
    assert rows["other"]["latest_check"]["ok"] is False


def test_one_monitor_shows_its_latest_check_too(api, monitor):
    add_check(monitor, ok=False)

    assert api.get(f"/monitors/{monitor.pk}/").json()["latest_check"]["ok"] is False


def test_a_monitor_just_created_has_no_latest_check(api):
    response = api.post("/monitors/", MONITOR_PAYLOAD, format="json")

    assert response.status_code == 201
    assert response.json()["latest_check"] is None


def test_patch_answers_with_the_latest_check(api, monitor):
    add_check(monitor, ok=True)

    body = api.patch(f"/monitors/{monitor.pk}/", {"timeout_s": 9}, format="json").json()

    assert body["latest_check"]["ok"] is True


def test_check_now_becomes_the_latest_check(api, monitor, fake_check):
    fake_check(ok=False, error_type="timeout")
    api.post(f"/monitors/{monitor.pk}/check/")

    latest = api.get(f"/monitors/{monitor.pk}/").json()["latest_check"]

    assert (latest["ok"], latest["error_type"]) == (False, "timeout")


@pytest.mark.parametrize("count", [1, 5])
def test_listing_monitors_takes_three_queries_however_many_there_are(
    api, db, django_assert_num_queries, count
):
    """No N+1: the token's user, COUNT, then one query for the page with every latest check in it."""
    make_monitors(count)

    with django_assert_num_queries(3):
        rows = api.get("/monitors/").json()["results"]

    assert [row["latest_check"]["ok"] for row in rows] == [False] * count
