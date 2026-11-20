"""A monitor exists only for the member it is assigned to. To any other member of the
organization its id answers 404, the same as an id that was never used: 403 would
confirm that the monitor is there. Other organizations: tests/orgs/test_tenancy.py."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from monitors import urls
from monitors.models import Check, Incident, Monitor
from tests.conftest import (
    MONITOR_PAYLOAD,
    assignee_problems,
    client_for,
    create_monitor,
    membership_of,
)

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
    """Another member of acme."""
    user = User.objects.create_user("intruder@example.com")
    membership_of(user)
    return user


@pytest.fixture
def intruder(other_user) -> APIClient:
    """Logged in with a valid token of their own, a member, assigned nothing."""
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


def test_the_assignee_still_reaches_their_own_monitor(api, monitor, fake_check):
    for method, route, body in BY_ID:
        if method != "delete":
            assert call(api, method, route, body, monitor.pk).status_code < 300
    assert call(api, "delete", "monitor-detail", None, monitor.pk).status_code == 204


@pytest.mark.parametrize("query", ["", "?after=0"], ids=["offset", "keyset"])
def test_the_list_holds_only_the_callers_monitors(
    api, intruder, monitor, other_user, query
):
    create_monitor(membership_of(other_user), name="theirs")

    mine = api.get(f"/monitors/{query}").json()
    theirs = intruder.get(f"/monitors/{query}").json()

    assert [row["name"] for row in mine["results"]] == ["backend"]
    assert [row["name"] for row in theirs["results"]] == ["theirs"]
    assert mine.get("count", 1) == 1  # the total does not count other people's either


def test_a_name_a_colleague_uses_is_taken_and_the_answer_does_not_say_whose(
    intruder, monitor
):
    """Unique in the organization (ROLE-3): a monitor changes assignee, never
    organization, so no transfer can ever clash on a name."""
    response = intruder.post(reverse("monitor-list"), MONITOR_PAYLOAD, format="json")

    assert response.status_code == 409
    assert response.json() == {
        "detail": f'A monitor named "{MONITOR_PAYLOAD["name"]}" already exists.'
    }
    assert Monitor.objects.filter(name=MONITOR_PAYLOAD["name"]).count() == 1


def test_renaming_to_a_colleagues_name_is_a_409_too(api, monitor, other_user):
    mine = create_monitor(name="mine")
    create_monitor(membership_of(other_user), name="theirs")

    response = api.patch(f"/monitors/{mine.pk}/", {"name": "theirs"}, format="json")

    assert response.status_code == 409
    assert response.json() == {"detail": 'A monitor named "theirs" already exists.'}


def test_another_organization_can_use_the_same_name(monitor, globex):
    with globex:
        create_monitor(None)
        assert Monitor.objects.filter(name=MONITOR_PAYLOAD["name"]).count() == 1
    assert Monitor.objects.filter(name=MONITOR_PAYLOAD["name"]).count() == 1


@pytest.mark.xfail(
    strict=True,
    reason="Deleting an account is not supported: users are deactivated. Deletion, "
    "which moves a leaver's monitors first, is ticket 3's (later.md).",
)
def test_deleting_an_account_leaves_no_monitor_assigned_to_a_dead_membership(
    monitor, user
):
    user.delete()

    assert assignee_problems() == []
