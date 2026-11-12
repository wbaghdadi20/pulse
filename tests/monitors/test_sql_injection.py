"""SQL injection, shown on a query written by hand and then fixed.

Pulse itself has no query like find_by_name_unsafe: the ORM sends every value as
a bound parameter, and so does the one raw statement (seed_checks). The two
functions below exist only here, to show what the difference is.
"""

import pytest
from django.db import ProgrammingError, connection, transaction

from accounts.models import User
from tests.conftest import create_monitor

pytestmark = pytest.mark.django_db

EVERYTHING = "' OR '1'='1"
OTHER_TABLE = "' UNION SELECT password_hash FROM users --"


def find_by_name_unsafe(owner_id: int, name: str) -> list[str]:
    """The mistake: the value is pasted into the SQL text, so a quote in it ends the
    string and whatever follows is run as SQL."""
    sql = f"SELECT name FROM monitors WHERE owner_id = {owner_id} AND name = '{name}'"
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]


def find_by_name(owner_id: int, name: str) -> list[str]:
    """The fix: the SQL and the values travel separately. The database is told
    "this is a value" and never parses it, whatever it contains."""
    sql = "SELECT name FROM monitors WHERE owner_id = %s AND name = %s"
    with connection.cursor() as cursor:
        cursor.execute(sql, [owner_id, name])
        return [row[0] for row in cursor.fetchall()]


@pytest.fixture
def victim() -> User:
    victim = User.objects.create_user("victim@example.com", "correct horse battery")
    create_monitor(owner=victim, name="victims-secret-project")
    return victim


@pytest.mark.parametrize("find", [find_by_name_unsafe, find_by_name])
def test_both_queries_find_a_monitor_by_its_name(find, monitor, user):
    assert find(user.pk, "backend") == ["backend"]
    assert find(user.pk, "no-such-name") == []


def test_the_formatted_query_returns_every_owners_monitors(monitor, user, victim):
    """WHERE owner_id = 1 AND name = '' OR '1'='1'  -- the OR is true for every row."""
    found = find_by_name_unsafe(user.pk, EVERYTHING)

    assert sorted(found) == ["backend", "victims-secret-project"]


def test_the_formatted_query_can_be_made_to_read_another_table(user, victim):
    found = find_by_name_unsafe(user.pk, OTHER_TABLE)

    assert victim.password in found  # a password hash, out through a "name" search


def test_the_formatted_query_breaks_on_an_ordinary_apostrophe(user):
    with pytest.raises(ProgrammingError), transaction.atomic():
        find_by_name_unsafe(user.pk, "O'Brien's API")


@pytest.mark.parametrize("attack", [EVERYTHING, OTHER_TABLE])
def test_with_bound_parameters_the_same_input_is_just_a_name_nobody_has(
    monitor, user, victim, attack
):
    assert find_by_name(user.pk, attack) == []


def test_with_bound_parameters_a_name_may_contain_anything(user):
    create_monitor(name="O'Brien's API")
    create_monitor(name=EVERYTHING)

    assert find_by_name(user.pk, "O'Brien's API") == ["O'Brien's API"]
    assert find_by_name(user.pk, EVERYTHING) == [EVERYTHING]


def test_the_api_stores_and_returns_an_attack_string_as_a_plain_name(api, victim):
    created = api.post(
        "/monitors/",
        {
            "name": EVERYTHING,
            "url": "http://example.com/",
            "timeout_s": 5,
            "interval_s": 30,
        },
        format="json",
    )

    assert created.status_code == 201
    assert [row["name"] for row in api.get("/monitors/").json()["results"]] == [
        EVERYTHING
    ]
