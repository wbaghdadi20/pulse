"""The backoff formula on its own: pure, so Hypothesis can drive every attempt and seed.

The task tests check the policy is applied; these check the policy itself.
"""

import random

from hypothesis import given
from hypothesis import strategies as st

from core.system_checks import MAX_ALERT_ATTEMPTS
from monitors.alerting import retry_delay

attempts = st.integers(min_value=1, max_value=MAX_ALERT_ATTEMPTS)
seeds = st.integers()


@given(attempt=attempts, seed=seeds)
def test_delay_is_the_exponential_floor_plus_at_most_the_same_again(attempt, seed):
    delay = retry_delay(attempt, rng=random.Random(seed))

    assert 2**attempt <= delay <= 2 ** (attempt + 1)


@given(seed=seeds)
def test_delays_never_shrink_between_consecutive_attempts(seed):
    rng = random.Random(seed)
    delays = [retry_delay(attempt, rng) for attempt in range(1, MAX_ALERT_ATTEMPTS + 1)]

    assert delays == sorted(delays)


@given(attempt=attempts, seed=seeds)
def test_the_same_random_state_gives_the_same_delay(attempt, seed):
    """Jitter is random, not arbitrary: a seeded run is reproducible."""
    assert retry_delay(attempt, random.Random(seed)) == retry_delay(
        attempt, random.Random(seed)
    )
