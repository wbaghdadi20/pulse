"""Mass assignment: a body can carry fields its sender has no business setting.

Nothing here needed a fix: a serializer only reads the fields it lists as writable,
and `owner` is not a field at all. These tests fail if that list ever grows by mistake.
"""

import pytest
from django.urls import reverse

from accounts.models import User
from monitors.models import Monitor
from tests.conftest import MONITOR_PAYLOAD, client_for, create_monitor

pytestmark = pytest.mark.django_db


@pytest.fixture
def victim() -> User:
    return User.objects.create_user("victim@example.com")


@pytest.mark.parametrize("field", ["owner", "owner_id", "user", "user_id"])
def test_a_monitor_belongs_to_its_creator_whatever_the_body_says(
    api, user, victim, field
):
    body = {**MONITOR_PAYLOAD, field: victim.pk}

    response = api.post(reverse("monitor-list"), body, format="json")

    assert response.status_code == 201
    assert Monitor.objects.get().owner == user
    assert not victim.monitors.exists()


@pytest.mark.parametrize("field", ["owner", "owner_id"])
def test_a_monitor_cannot_be_given_away_or_taken_by_editing_it(
    api, user, victim, monitor, field
):
    theirs = create_monitor(owner=victim, name="theirs")

    api.patch(
        reverse("monitor-detail", args=[monitor.pk]), {field: victim.pk}, format="json"
    )
    client_for(victim).patch(
        reverse("monitor-detail", args=[theirs.pk]), {field: user.pk}, format="json"
    )

    monitor.refresh_from_db()
    theirs.refresh_from_db()
    assert (monitor.owner, theirs.owner) == (user, victim)


def test_fields_the_server_sets_cannot_be_set_from_the_body(api):
    body = {
        **MONITOR_PAYLOAD,
        "id": 4242,
        "created_at": "2001-01-01T00:00:00Z",
        "last_checked_at": "2001-01-01T00:00:00Z",
        "next_check_at": "2099-01-01T00:00:00Z",
        "latest_check": {"ok": True},
    }

    created = api.post(reverse("monitor-list"), body, format="json").json()

    assert created["id"] != 4242
    assert created["created_at"] > "2026"
    assert created["last_checked_at"] is None
    assert created["next_check_at"] < "2099"
    assert created["latest_check"] is None


def test_signup_only_reads_the_email_and_the_password(anon):
    body = {
        "email": "walid@example.com",
        "password": "correct horse battery",
        "id": 4242,
        "created_at": "2001-01-01T00:00:00Z",
        "password_hash": "argon2$forged",
        "is_staff": True,
        "is_superuser": True,
    }

    assert anon.post("/signup", body, format="json").status_code == 201

    user = User.objects.get()
    assert user.pk != 4242
    assert user.created_at.year >= 2026
    assert user.check_password("correct horse battery")
    assert not hasattr(user, "is_staff")
