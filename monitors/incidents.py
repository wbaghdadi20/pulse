"""The incident state machine: decides when a monitor is really down.

Pure logic: no Django, no database. Callers load the recent check results and
apply the returned action (see services.py). Keeping it pure makes it trivial
to test and lets D5's mutation testing run against it in isolation.
"""

from collections.abc import Sequence
from enum import Enum


class Action(Enum):
    OPEN = "open"
    RESOLVE = "resolve"
    NOTHING = "nothing"


def decide(
    results: Sequence[bool],
    incident_open: bool,
    *,
    fail_threshold: int,
    ok_threshold: int,
) -> Action:
    """Return what should happen given the recent results (oldest first).

    - No open incident: open one after `fail_threshold` failures in a row.
    - Open incident: resolve it after `ok_threshold` successes in a row.
    - Anything else (a blip, not enough results yet): nothing.
    """
    if fail_threshold < 1 or ok_threshold < 1:
        raise ValueError("threshold must be at least 1")

    if incident_open:
        if _last_n_all(results, ok_threshold, value=True):
            return Action.RESOLVE
        return Action.NOTHING

    if _last_n_all(results, fail_threshold, value=False):
        return Action.OPEN
    return Action.NOTHING


def _last_n_all(results: Sequence[bool], n: int, *, value: bool) -> bool:
    """True if there are at least n results and the most recent n all equal value."""
    if len(results) < n:
        return False
    return all(r is value for r in results[-n:])
