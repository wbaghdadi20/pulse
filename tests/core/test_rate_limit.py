"""Rate limiting: the counting on its own, then through the API as 429 + Retry-After."""

from functools import partial

import pytest

from core import throttling
from core.ratelimit import Decision, fixed_window
from tests.conftest import client_for

MINUTE = 1_800_000_000 // 60 * 60  # a timestamp exactly on a minute boundary

# --- the fixed window, with the clock passed in ------------------------------------


def test_requests_up_to_the_limit_are_allowed_and_the_next_is_not():
    answers = [fixed_window("alice", 3, now=MINUTE + 1).allowed for _ in range(5)]

    assert answers == [True, True, True, False, False]


def test_retry_after_is_the_time_left_in_the_minute():
    assert fixed_window("alice", 1, now=MINUTE).retry_after == 60
    assert fixed_window("alice", 1, now=MINUTE + 15).retry_after == 45
    assert fixed_window("alice", 1, now=MINUTE + 59.9).retry_after == 1


def test_the_count_starts_again_in_the_next_minute():
    for _ in range(4):
        fixed_window("alice", 3, now=MINUTE + 59)

    assert fixed_window("alice", 3, now=MINUTE + 60).allowed


def test_each_client_has_its_own_count():
    for _ in range(4):
        fixed_window("alice", 3, now=MINUTE)

    assert fixed_window("bob", 3, now=MINUTE).allowed


def test_a_fixed_window_lets_twice_the_limit_through_across_a_boundary():
    """The known weakness: documented in SECURITY.md, pinned here so it is no surprise."""
    end_of_minute = [
        fixed_window("alice", 3, now=MINUTE + 59).allowed for _ in range(3)
    ]
    start_of_next = [
        fixed_window("alice", 3, now=MINUTE + 60).allowed for _ in range(3)
    ]

    assert end_of_minute + start_of_next == [True] * 6


class RecordingCache:
    """Remembers the key and lifetime of every counter it is asked to create."""

    def __init__(self) -> None:
        self.created: dict[str, int] = {}

    def add(self, key: str, value: int, timeout: int) -> None:
        self.created[key] = timeout

    def incr(self, key: str) -> int:
        return 1


def test_the_counter_expires_instead_of_staying_in_the_cache_forever(monkeypatch):
    recording = RecordingCache()
    monkeypatch.setattr("core.ratelimit.cache", recording)

    fixed_window("alice", 3, now=MINUTE)

    assert recording.created == {f"rl:alice:{MINUTE // 60}": 60}


# --- through the API ---------------------------------------------------------------

pytestmark = pytest.mark.django_db
WRONG = {"email": "walid@example.com", "password": "not-the-password"}


@pytest.fixture(autouse=True)
def one_clock_minute(monkeypatch) -> None:
    """Every request of a test lands in the same window. On the real clock, a run
    that crosses a minute boundary starts the count again in the middle of a test."""
    monkeypatch.setattr(
        throttling.RateLimitThrottle,
        "limiter",
        staticmethod(partial(fixed_window, now=MINUTE + 30)),
    )


def test_the_sixth_login_in_a_minute_is_a_429_with_retry_after(anon, settings):
    codes = [anon.post("/login", WRONG).status_code for _ in range(5)]
    blocked = anon.post("/login", WRONG)

    assert codes == [401] * settings.PULSE_AUTH_RATE_LIMIT_PER_MINUTE
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60


def test_a_blocked_login_never_reaches_the_password_check(anon, monkeypatch):
    checked = []
    monkeypatch.setattr(
        "accounts.services.authenticate", lambda **kwargs: checked.append(kwargs)
    )

    for _ in range(8):
        anon.post("/login", WRONG)

    assert len(checked) == 5


def test_signup_shares_the_strict_limit_with_login(anon):
    for _ in range(5):
        anon.post("/login", WRONG)

    assert anon.post("/signup", WRONG).status_code == 429


def test_the_api_limit_is_the_configured_number_per_minute(api, settings):
    settings.PULSE_RATE_LIMIT_PER_MINUTE = 3

    codes = [api.get("/monitors/").status_code for _ in range(5)]

    assert codes == [200, 200, 200, 429, 429]


def test_the_login_limit_does_not_use_up_the_api_limit(api, anon):
    for _ in range(6):
        anon.post("/login", WRONG)

    assert api.get("/monitors/").status_code == 200


def test_another_address_has_its_own_count(user, settings):
    settings.PULSE_RATE_LIMIT_PER_MINUTE = 1
    here, elsewhere = client_for(user), client_for(user)

    assert here.get("/monitors/").status_code == 200
    assert here.get("/monitors/").status_code == 429
    assert elsewhere.get("/monitors/", REMOTE_ADDR="203.0.113.9").status_code == 200


def test_a_made_up_forwarded_for_header_does_not_buy_a_new_count(anon):
    """X-Forwarded-For is whatever the client types. With no proxy configured it is ignored."""
    codes = [
        anon.post("/login", WRONG, HTTP_X_FORWARDED_FOR=f"198.51.100.{n}").status_code
        for n in range(6)
    ]

    assert codes == [401] * 5 + [429]


def test_behind_one_proxy_the_address_the_proxy_added_identifies_the_client(
    anon, settings
):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}

    def login(real_client: str) -> int:
        # The client wrote "1.2.3.4"; the proxy appended the address it really saw.
        forwarded = f"1.2.3.4, {real_client}"
        response = anon.post("/login", WRONG, HTTP_X_FORWARDED_FOR=forwarded)
        return int(response.status_code)

    assert [login("198.51.100.7") for _ in range(6)] == [401] * 5 + [429]
    assert login("198.51.100.8") == 401


@pytest.mark.parametrize("path", ["/health", "/status"])
def test_health_and_status_are_never_rate_limited(anon, settings, path):
    settings.PULSE_RATE_LIMIT_PER_MINUTE = 1

    assert {anon.get(path).status_code for _ in range(10)} == {200}


# --- the strategy ------------------------------------------------------------------


def test_the_algorithm_can_be_swapped_without_touching_the_throttle(api, monkeypatch):
    """Any function with the Limiter signature works: here, one that refuses everyone."""

    def closed(key: str, limit: int) -> Decision:
        return Decision(allowed=False, retry_after=7)

    monkeypatch.setattr(throttling.RateLimitThrottle, "limiter", staticmethod(closed))

    response = api.get("/monitors/")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
