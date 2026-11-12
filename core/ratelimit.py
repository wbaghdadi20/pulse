"""Counting requests per client.

The algorithm is a strategy: a Limiter is any function with fixed_window's first two
parameters and its return type, and core/throttling.py is handed one. Only the
fixed window exists. Why, and what the others would change: docs/adr/0016-rate-limiting.md
"""

import time
from collections.abc import Callable
from typing import NamedTuple

from django.core.cache import cache

WINDOW_S = 60


class Decision(NamedTuple):
    allowed: bool
    retry_after: int  # seconds until a request will be allowed again


# (who is asking, how many they may make per minute) -> may this one go ahead?
Limiter = Callable[[str, int], Decision]


def fixed_window(key: str, limit: int, now: float | None = None) -> Decision:
    """One counter per client per clock minute; over `limit`, wait for the next minute.

    add() creates the counter with its expiry the first time and does nothing after;
    incr() is a single INCR in Redis, so two requests arriving together cannot both
    read the same count. The counter outlives its minute by less than a minute.
    """
    window, elapsed = divmod(int(time.time() if now is None else now), WINDOW_S)
    counter = f"rl:{key}:{window}"
    cache.add(counter, 0, timeout=WINDOW_S)
    count: int = cache.incr(counter)
    return Decision(allowed=count <= limit, retry_after=WINDOW_S - elapsed)
