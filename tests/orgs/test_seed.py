"""make seed: two demo organizations with an owner each, shop's monitors in acme."""

import re
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from monitors.models import Monitor
from orgs.models import Membership

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(settings) -> None:
    settings.DEBUG = True
    call_command("seed_demo", stdout=None)
    call_command("seed_demo", stdout=None)  # again: nothing is added twice


def test_each_demo_organization_has_an_owner(seeded):
    owners = Membership.objects.filter(role="owner", user__email__endswith=".test")

    assert sorted(owners.values_list("organization__slug", "user__email")) == [
        ("acme", "owner@acme.test"),
        ("globex", "owner@globex.test"),
    ]


def test_shops_monitors_are_public_in_acme_and_assigned_to_its_owner(seeded, globex):
    owner = Membership.objects.get(user__email="owner@acme.test")

    assert sorted(
        Monitor.objects.filter(is_public=True).values_list("name", "assignee_id")
    ) == [("shop", owner.pk), ("shop deep", owner.pk)]
    with globex:
        assert not Monitor.objects.exists()


def test_the_seed_refuses_to_run_without_debug(settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="Development only"):
        call_command("seed_demo", stdout=None)
    assert not Membership.objects.filter(user__email__endswith=".test").exists()


def test_make_seed_runs_this_command():
    makefile = (ROOT / "Makefile").read_text()

    assert re.search(r"^seed:.*\n\t.*manage\.py seed_demo$", makefile, re.M)
