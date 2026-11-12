"""A monitor exists only for its owner. To anyone else its id answers 404, the same
as an id that was never used: 403 would confirm that the monitor is there."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from monitors import urls
from monitors.models import Check, Incident, Monitor
from tests.conftest import MONITOR_PAYLOAD, client_for, create_monitor

pytestmark = pytest.mark.django_db

# Every way to reach one monitor by its id: (method, route name, body).
BY_ID = [
    ("get", "monitor-detail", None),
    ("patch", "monitor-detail", {"name": "taken-over"}),
    ("delete", "monitor-detail", None),
    ("post", "monitor-check", None),
    ("get", "monitor-uptime", None),
    ("post", "monitor-incidents", {"cause": "planted"}),
]


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user("intruder@example.com")


@pytest.fixture
def intruder(other_user) -> APIClient:
    """Logged in with a valid token of their own, and owning nothing."""
    return client_for(other_user)


def call(client: APIClient, method: str, route: str, body, pk: int):
    return getattr(client, method)(reverse(route, args=[pk]), body, format="json")


@pytest.mark.parametrize("method, route, body", BY_ID)
def test_someone_elses_monitor_answers_404(
    intruder, monitor, fake_check, method, route, body
):
    response = call(intruder, method, route, body, monitor.pk)

    assert response.status_code == 404


@pytest.mark.parametrize("method, route, body", BY_ID)
def test_the_answer_is_the_same_as_for_an_id_never_used(
    intruder, monitor, fake_check, method, route, body
):
    never_used = monitor.pk + 1000

    theirs = call(intruder, method, route, body, monitor.pk)
    missing = call(intruder, method, route, body, never_used)

    assert theirs.status_code == missing.status_code
    assert theirs.json() == {"detail": f"Monitor {monitor.pk} not found."}
    assert missing.json() == {"detail": f"Monitor {never_used} not found."}


def test_none_of_the_attempts_changed_anything(intruder, monitor, fake_check):
    for method, route, body in BY_ID:
        call(intruder, method, route, body, monitor.pk)

    monitor.refresh_from_db()  # still there: the DELETE did not delete
    assert monitor.name == MONITOR_PAYLOAD["name"]
    assert fake_check.calls == []
    assert not Check.objects.exists()
    assert not Incident.objects.exists()


def test_every_route_that_takes_a_monitor_id_is_in_the_list_above():
    """A new /monitors/<pk>/... route fails here until it is added to BY_ID."""
    taking_an_id = {
        entry.name for entry in urls.urlpatterns if "pk" in entry.pattern.converters
    }

    assert taking_an_id == {route for _, route, _ in BY_ID}


def test_the_owner_still_reaches_their_own_monitor(api, monitor, fake_check):
    for method, route, body in BY_ID:
        if method != "delete":
            assert call(api, method, route, body, monitor.pk).status_code < 300
    assert call(api, "delete", "monitor-detail", None, monitor.pk).status_code == 204


@pytest.mark.parametrize("query", ["", "?after=0"], ids=["offset", "keyset"])
def test_the_list_holds_only_the_callers_monitors(
    api, intruder, monitor, other_user, query
):
    create_monitor(owner=other_user, name="theirs")

    mine = api.get(f"/monitors/{query}").json()
    theirs = intruder.get(f"/monitors/{query}").json()

    assert [row["name"] for row in mine["results"]] == ["backend"]
    assert [row["name"] for row in theirs["results"]] == ["theirs"]
    assert mine.get("count", 1) == 1  # the total does not count other people's either


def test_two_owners_can_use_the_same_name(intruder, monitor):
    """Unique per owner: a 409 here would reveal a name someone else uses."""
    response = intruder.post(reverse("monitor-list"), MONITOR_PAYLOAD, format="json")

    assert response.status_code == 201
    assert Monitor.objects.filter(name=MONITOR_PAYLOAD["name"]).count() == 2


def test_deleting_an_account_deletes_its_monitors(monitor, user):
    user.delete()

    assert not Monitor.objects.exists()
