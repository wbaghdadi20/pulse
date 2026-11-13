"""/health says the process answers; /ready says its dependencies answer too."""

import logging

import pytest
import redis
from django.conf import settings

from core import readiness


def down() -> None:
    raise ConnectionError("connection to server at postgres (172.18.0.3) refused")


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    """Both checks replaced by ones that pass and write their name here."""
    made: list[str] = []
    for name in list(readiness.CHECKS):
        monkeypatch.setitem(readiness.CHECKS, name, lambda name=name: made.append(name))
    return made


# --- /health ------------------------------------------------------------------------


def test_health_asks_nothing_of_postgres_or_redis(anon, calls):
    """No database fixture either: a query here would fail the test."""
    assert anon.get("/health").status_code == 200
    assert calls == []


def test_health_stays_200_while_the_dependencies_are_down(anon, monkeypatch):
    monkeypatch.setitem(readiness.CHECKS, "database", down)
    monkeypatch.setitem(readiness.CHECKS, "redis", down)

    assert anon.get("/health").status_code == 200


# --- /ready -------------------------------------------------------------------------


@pytest.mark.django_db
def test_ready_is_200_when_postgres_and_redis_answer(anon):
    """The real checks, against the test database and the Redis the suite uses."""
    response = anon.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_ready_asks_both(anon, calls):
    anon.get("/ready")

    assert calls == ["database", "redis"]


@pytest.mark.parametrize(
    ("broken", "checks"),
    [
        (["database"], {"database": "failed", "redis": "ok"}),
        (["redis"], {"database": "ok", "redis": "failed"}),
        (["database", "redis"], {"database": "failed", "redis": "failed"}),
    ],
)
def test_ready_is_503_and_names_what_failed(anon, calls, monkeypatch, broken, checks):
    for name in broken:
        monkeypatch.setitem(readiness.CHECKS, name, down)

    response = anon.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": checks}


def test_the_reason_goes_to_the_log_and_not_into_the_response(
    anon, calls, monkeypatch, caplog
):
    monkeypatch.setitem(readiness.CHECKS, "database", down)

    with caplog.at_level(logging.WARNING):
        response = anon.get("/ready")

    assert "172.18.0.3" not in response.content.decode()
    (record,) = [r for r in caplog.records if r.name == "core.readiness"]
    assert record.getMessage() == "readiness: database failed"
    assert record.exc_info is not None
    assert "172.18.0.3" in str(record.exc_info[1])


def test_ready_is_public_and_never_rate_limited(anon, calls, settings):
    settings.PULSE_RATE_LIMIT_PER_MINUTE = 1

    assert {anon.get("/ready").status_code for _ in range(10)} == {200}


def test_ready_rejects_other_methods(anon):
    assert anon.post("/ready").status_code == 405


# --- the checks themselves ----------------------------------------------------------


def test_the_redis_check_raises_when_nothing_listens(settings):
    settings.PULSE_REDIS_URL = "redis://127.0.0.1:1/0"

    with pytest.raises(redis.RedisError):
        readiness.redis_ping()


def test_connecting_to_postgres_gives_up_after_the_dependency_timeout():
    options = settings.DATABASES["default"]["OPTIONS"]

    assert options == {"connect_timeout": settings.PULSE_DEPENDENCY_TIMEOUT_S}
