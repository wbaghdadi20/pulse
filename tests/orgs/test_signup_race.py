"""Two signups at the same moment need two connections, so what they write is
committed: no test transaction here, and the fixture cleans up (TEN-1)."""

import threading
from collections.abc import Iterator

import pytest
from django.db import connection

from accounts import services as accounts
from accounts.errors import EmailTaken
from accounts.models import User
from orgs.errors import SlugTaken
from orgs.models import Organization
from tests.accounts.test_signup_organization import SIGNUP
from tests.conftest import on_test_database

SLUGS = ["first", "second"]
EMAILS = ["first@example.com", "second@example.com"]


def clean_up() -> None:
    for org in Organization.objects.filter(slug__in=SLUGS):
        org.delete(force_drop=True)
    User.objects.filter(email__in=[SIGNUP["email"], *EMAILS]).delete()


@pytest.fixture
def at_once(django_db_setup, django_db_blocker) -> Iterator[None]:
    """Committed signups: their organizations, schemas and accounts are deleted
    before (an interrupted run) and after."""
    with django_db_blocker.unblock():
        assert on_test_database(), connection.settings_dict["NAME"]
        clean_up()
        try:
            yield
        finally:
            clean_up()


def sign_up_together(*signups: dict[str, str]) -> list[str]:
    barrier = threading.Barrier(len(signups))
    outcomes: list[str] = []

    def sign_up(fields: dict[str, str]) -> None:
        barrier.wait()
        try:
            accounts.signup(**fields)
            outcomes.append("created")
        except EmailTaken:
            outcomes.append("email taken")
        except SlugTaken:
            outcomes.append("slug taken")
        finally:
            connection.close()

    threads = [threading.Thread(target=sign_up, args=(s,)) for s in signups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return sorted(outcomes)


def test_two_signups_with_one_email_at_the_same_moment_make_one_account(at_once):
    outcomes = sign_up_together(*({**SIGNUP, "slug": slug} for slug in SLUGS))

    assert outcomes == ["created", "email taken"]
    assert User.objects.filter(email=SIGNUP["email"]).count() == 1
    assert Organization.objects.filter(slug__in=SLUGS).count() == 1


def test_two_signups_with_one_slug_at_the_same_moment_make_one_organization(at_once):
    """Neither sees the other's row before inserting: the constraint decides."""
    outcomes = sign_up_together(
        *({**SIGNUP, "email": email, "slug": "first"} for email in EMAILS)
    )

    assert outcomes == ["created", "slug taken"]
    assert Organization.objects.filter(slug="first").count() == 1
    assert User.objects.filter(email__in=EMAILS).count() == 1
