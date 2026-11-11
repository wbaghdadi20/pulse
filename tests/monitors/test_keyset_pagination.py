"""GET /monitors/?after=<id>: keyset pagination next to limit/offset."""

from urllib.parse import parse_qs, urlsplit

import pytest

from monitors.models import Monitor
from tests.conftest import MONITOR_PAYLOAD

pytestmark = pytest.mark.django_db


@pytest.fixture
def monitors() -> list[Monitor]:
    return [
        Monitor.objects.create(**{**MONITOR_PAYLOAD, "name": f"m{n}"}) for n in range(5)
    ]


def ids(response) -> list[int]:
    return [row["id"] for row in response.json()["results"]]


def test_the_first_page_starts_after_zero(api, monitors):
    response = api.get("/monitors/?after=0&limit=2")

    assert response.status_code == 200
    assert ids(response) == [monitors[0].pk, monitors[1].pk]


def test_next_points_after_the_last_row_of_the_page(api, monitors):
    body = api.get("/monitors/?after=0&limit=2").json()

    query = parse_qs(urlsplit(body["next"]).query)
    assert query == {"after": [str(monitors[1].pk)], "limit": ["2"]}


def test_following_next_visits_every_monitor_once(api, monitors):
    seen: list[int] = []
    url = "/monitors/?after=0&limit=2"
    while url:
        response = api.get(url)
        seen += ids(response)
        url = response.json()["next"]

    assert seen == [m.pk for m in monitors]


def test_the_last_page_has_no_next(api, monitors):
    body = api.get(f"/monitors/?after={monitors[2].pk}&limit=2").json()

    assert [row["id"] for row in body["results"]] == [monitors[3].pk, monitors[4].pk]
    assert body["next"] is None


def test_a_page_past_the_end_is_empty_not_an_error(api, monitors):
    body = api.get(f"/monitors/?after={monitors[-1].pk}").json()

    assert body == {"next": None, "results": []}


def test_there_is_no_count_and_no_count_query(api, monitors, django_assert_num_queries):
    with django_assert_num_queries(1):
        body = api.get("/monitors/?after=0&limit=2").json()

    assert set(body) == {"next", "results"}


def test_a_row_deleted_between_pages_does_not_shift_the_next_page(api, monitors):
    """With offset, deleting a row already seen makes the next page skip one."""
    first = api.get("/monitors/?after=0&limit=2")
    monitors[0].delete()

    by_offset = api.get("/monitors/?limit=2&offset=2")
    by_keyset = api.get(first.json()["next"])

    assert ids(by_offset) == [monitors[3].pk, monitors[4].pk], "m2 was never shown"
    assert ids(by_keyset) == [monitors[2].pk, monitors[3].pk]


def test_limit_defaults_and_is_capped_like_offset_pagination(api, db):
    Monitor.objects.bulk_create(
        Monitor(**{**MONITOR_PAYLOAD, "name": f"m{n}"}) for n in range(105)
    )

    assert len(api.get("/monitors/?after=0").json()["results"]) == 20
    assert len(api.get("/monitors/?after=0&limit=1000").json()["results"]) == 100


@pytest.mark.parametrize("after", ["abc", "-1", "1.5", ""])
def test_a_malformed_after_is_400(api, after):
    response = api.get(f"/monitors/?after={after}")

    assert response.status_code == 400
    assert "after" in response.json()


def test_without_after_the_list_is_paginated_by_offset_as_before(api, monitors):
    body = api.get("/monitors/?limit=2").json()

    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 5
