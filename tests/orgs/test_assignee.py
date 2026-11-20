"""A monitor's assignee: a membership id with no database constraint behind it, so
the services keep it valid (docs/adr/0026, TEN-2)."""

import pytest

from accounts.models import User
from monitors import services
from monitors.models import Monitor
from orgs.errors import NotAnActiveMember
from orgs.models import Membership, Organization
from orgs.services import require_active_member
from tests.conftest import (
    MONITOR_PAYLOAD,
    assignee_problems,
    client_for,
    create_monitor,
    membership_of,
    use_public,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(acme) -> Membership:
    return membership_of(User.objects.create_user("alice@example.com"), acme)


@pytest.fixture
def gina(globex) -> Membership:
    return membership_of(User.objects.create_user("gina@example.com"), globex)


def test_a_monitor_can_be_assigned_to_an_active_member_of_its_organization(alice):
    monitor = services.create_monitor(alice, {**MONITOR_PAYLOAD})

    assert monitor.assignee == alice
    assert assignee_problems() == []


def test_a_monitor_can_be_assigned_to_nobody(db):
    assert services.create_monitor(None, {**MONITOR_PAYLOAD}).assignee is None
    assert assignee_problems() == []


def test_a_member_of_another_organization_cannot_be_assigned(gina):
    with pytest.raises(NotAnActiveMember):
        services.create_monitor(gina, {**MONITOR_PAYLOAD})
    assert not Monitor.objects.exists()


def test_a_monitor_cannot_be_reassigned_to_a_member_of_another_organization(
    alice, gina
):
    monitor = services.create_monitor(alice, {**MONITOR_PAYLOAD})

    with pytest.raises(NotAnActiveMember):
        services.update_monitor(monitor, {"assignee": gina})
    monitor.refresh_from_db()
    assert monitor.assignee == alice
    assert assignee_problems() == []


def test_creating_and_editing_through_the_api_keeps_every_assignee_valid(alice, gina):
    assert alice.user
    api = client_for(alice.user)
    created = api.post("/monitors/", {**MONITOR_PAYLOAD, "assignee": gina.pk}, "json")
    api.patch(f"/monitors/{created.json()['id']}/", {"assignee_id": gina.pk}, "json")

    assert assignee_problems() == []


def test_a_membership_that_does_not_exist_cannot_be_assigned(db):
    with pytest.raises(NotAnActiveMember):
        require_active_member(10**9)


def test_a_membership_whose_account_is_gone_cannot_be_assigned(alice):
    assert alice.user
    alice.user.delete()

    with pytest.raises(NotAnActiveMember):
        require_active_member(alice.pk)


def test_nothing_can_be_assigned_outside_an_organization(alice):
    use_public()

    with pytest.raises(NotAnActiveMember):
        require_active_member(alice.pk)


def test_the_checker_finds_a_monitor_pointing_at_another_organization(gina):
    Monitor.objects.create(assignee=gina, **MONITOR_PAYLOAD)  # past the services

    assert assignee_problems() == [
        f"acme: monitor {Monitor.objects.get().pk} -> membership {gina.pk}"
    ]


def test_deleting_a_membership_in_public_never_touches_an_organization(alice):
    """No foreign key from a tenant schema: the delete runs without visiting acme."""
    monitor = create_monitor(alice)
    use_public()

    Membership.objects.filter(pk=alice.pk).delete()

    with Organization.objects.get(slug="acme"):
        assert Monitor.objects.get(pk=monitor.pk).assignee_id == alice.pk
