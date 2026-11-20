"""Signups at the same moment need two connections, so what they write is committed:
no test transaction here, and the fixture cleans up (TEN-1)."""

import threading
from collections.abc import Iterator

import pytest
from django.db import connection
from psycopg import sql

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
    """Also after a run cut short halfway: a schema may already be gone."""
    for schema in Organization.objects.filter(slug__in=SLUGS).values_list(
        "schema_name", flat=True
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE")
                .format(sql.Identifier(schema))
                .as_string()
            )
    Organization.objects.filter(slug__in=SLUGS).delete()  # rows only: no schema left
    User.objects.filter(email__in=[SIGNUP["email"], *EMAILS]).delete()


@pytest.fixture
def at_once(django_db_setup, django_db_blocker, settings) -> Iterator[None]:
    """Committed signups: their organizations, schemas and accounts are deleted
    before (an interrupted run) and after. The loser of a race waits for the winner
    to commit, which a slow machine can make long: here it may wait 30 s."""
    settings.PULSE_SIGNUP_LOCK_TIMEOUT_MS = 30_000
    with django_db_blocker.unblock():
        assert on_test_database(), connection.settings_dict["NAME"]
        clean_up()
        try:
            yield
        finally:
            clean_up()


def sign_up_together(*signups: dict[str, str]) -> list[str]:
    """What happened to each signup, sorted: created, email taken, slug taken, or
    the name of any other exception."""
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
        except Exception as exc:
            outcomes.append(f"{type(exc).__name__}: {exc}")
        finally:
            connection.close()

    threads = [threading.Thread(target=sign_up, args=(s,)) for s in signups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a signup never finished"
    return sorted(outcomes)


def test_two_signups_with_one_email_at_the_same_moment_make_one_account(at_once):
    outcomes = sign_up_together(*({**SIGNUP, "slug": slug} for slug in SLUGS))

    assert outcomes == ["created", "email taken"]
    assert User.objects.filter(email=SIGNUP["email"]).count() == 1
    assert Organization.objects.filter(slug__in=SLUGS).count() == 1


def test_two_signups_with_one_slug_at_the_same_moment_make_one_organization(at_once):
    """The second waits for its turn, then finds the slug taken."""
    outcomes = sign_up_together(
        *({**SIGNUP, "email": email, "slug": "first"} for email in EMAILS)
    )

    assert outcomes == ["created", "slug taken"]
    assert Organization.objects.filter(slug="first").count() == 1
    assert User.objects.filter(email__in=EMAILS).count() == 1


def test_the_first_signups_in_a_new_database_take_turns_instead_of_failing(at_once):
    """Before the first clone the clone function's type does not exist yet, and
    two clones creating it at once collided on pg_type's unique index."""
    with connection.cursor() as cursor:
        cursor.execute("DROP TYPE IF EXISTS public.cloneparms CASCADE")

    outcomes = sign_up_together(
        *(
            {**SIGNUP, "email": email, "slug": slug}
            for email, slug in zip(EMAILS, SLUGS, strict=True)
        )
    )

    assert outcomes == ["created", "created"]
