"""Latency buckets: where a latency is counted, and the percentile read back from the counts."""

import math
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from monitors import latency

LATENCIES = st.integers(min_value=0, max_value=75_000)  # ms: past the 75 s kill


def bucket_of(latency_ms: int) -> int:
    """latency.BUCKET_SQL, in Python."""
    return math.floor(math.log(max(latency_ms, 1)) / math.log(latency.GROWTH))


@given(st.lists(LATENCIES, min_size=1, max_size=200))
def test_p95_from_buckets_is_within_5_percent_of_the_real_one(values):
    counts = Counter(bucket_of(value) for value in values)
    real = sorted(values)[max(1, math.ceil(0.95 * len(values) - 1e-9)) - 1]

    estimate = latency.percentile(counts, 0.95)

    assert estimate == pytest.approx(max(real, 1), rel=0.05)


@given(
    st.lists(LATENCIES, min_size=1, max_size=50),
    st.lists(LATENCIES, min_size=1, max_size=50),
)
def test_counts_of_two_hours_added_up_give_the_percentile_of_both(first, second):
    """What a percentile per hour could not do."""
    together = Counter(bucket_of(v) for v in first) + Counter(
        bucket_of(v) for v in second
    )
    all_at_once = Counter(bucket_of(v) for v in first + second)

    assert latency.percentile(together, 0.95) == latency.percentile(all_at_once, 0.95)


def test_the_percentile_of_one_latency_is_its_bucket():
    assert latency.percentile({48: 1}, 0.95) == latency.value_of(48)
    assert latency.value_of(48) == pytest.approx(100, rel=0.05)


def test_the_rank_is_not_pushed_up_by_rounding():
    """0.95 * 40 is 38.00000000000001 in floating point. The 38th of 40 is wanted."""
    assert latency.percentile({48: 38, 90: 2}, 0.95) == latency.value_of(48)
    assert latency.percentile({48: 37, 90: 3}, 0.95) == latency.value_of(90)


def test_a_small_fraction_still_means_the_first_latency():
    assert latency.percentile({10: 1, 20: 1}, 0.0) == latency.value_of(10)


def test_no_counts_is_an_error():
    with pytest.raises(ValueError, match="no latencies"):
        latency.percentile({}, 0.95)
