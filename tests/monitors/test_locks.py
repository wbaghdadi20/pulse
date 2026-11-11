"""The Redis lock, against a real Redis (the `redis_db` fixture)."""

import time

import pytest

from monitors import locks

pytestmark = pytest.mark.usefixtures("redis_db")

KEY = "lock:test"


def take() -> str:
    token = locks.acquire(KEY, 10)
    assert token is not None
    return token


def expire_now(redis_db) -> None:
    redis_db.pexpire(KEY, 1)
    time.sleep(0.02)
    assert not redis_db.exists(KEY)


def test_only_one_caller_gets_the_lock():
    take()

    assert locks.acquire(KEY, 10) is None


def test_a_released_lock_can_be_taken_again():
    token = take()

    assert locks.release(KEY, token) is True
    assert locks.acquire(KEY, 10) is not None


def test_every_holder_gets_its_own_token():
    first = take()
    locks.release(KEY, first)

    assert take() != first


def test_the_lock_frees_itself_after_its_ttl(redis_db):
    take()

    assert 0 < redis_db.ttl(KEY) <= 10


def test_a_holder_whose_lock_expired_cannot_release_the_next_holders_lock(redis_db):
    """The lock-expiry bug: A's lock times out mid-check, B takes it, then A finishes."""
    a = take()
    expire_now(redis_db)
    b = take()  # expired, so B gets in: two workers now think they hold the lock

    assert locks.release(KEY, a) is False, "A is done, but the lock is B's now"

    assert locks.acquire(KEY, 10) is None, "B's lock survived: no third worker"
    assert locks.release(KEY, b) is True


def test_releasing_a_lock_nobody_holds_does_nothing():
    assert locks.release(KEY, "no-such-token") is False
