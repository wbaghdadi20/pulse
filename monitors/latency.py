"""Latencies counted in buckets, so that a percentile can be taken over hours that
were summed up separately. Two percentiles cannot be combined into one; two sets of
bucket counts can, by adding them.

Bucket b holds latencies from GROWTH**b up to GROWTH**(b + 1) ms: each bucket is
10% wider than the one before, so a latency is known to within 5% of itself
however large it is. A 30 s timeout lands in bucket 108.
"""

import math
from collections.abc import Mapping

GROWTH = 1.1

# The bucket of a row of `checks`, in SQL. One spelling, used by the roll-up
# statement and by the query over checks not rolled up yet.
BUCKET_SQL = f"floor(ln(greatest(latency_ms, 1)::float8) / ln({GROWTH}::float8))::int"


def value_of(bucket: int) -> float:
    """The latency a bucket stands for: its middle, on the scale the buckets grow on."""
    return float(GROWTH ** (bucket + 0.5))


def percentile(counts: Mapping[int, int], fraction: float) -> float:
    """The value at `fraction` of the way through the counted latencies (nearest
    rank), as the middle of the bucket it falls in. `counts` must not be empty."""
    # The smallest rank with at least `fraction` of the latencies at or below it.
    # The epsilon keeps 0.95 * 40 = 38.00000000000001 from becoming rank 39.
    rank = max(1, math.ceil(fraction * sum(counts.values()) - 1e-9))
    seen = 0
    for bucket in sorted(counts):
        seen += counts[bucket]
        if seen >= rank:
            return value_of(bucket)
    raise ValueError("no latencies were counted")
