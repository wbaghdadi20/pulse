"""Incident state machine, tested without Django or a database.

Results are oldest first, most recent last. N = failures in a row to open,
M = successes in a row to resolve.
"""

import pytest

from monitors.incidents import Action, decide

OK, FAIL = True, False
N, M = 3, 2


def decide_(results, incident_open=False):
    return decide(results, incident_open, fail_threshold=N, ok_threshold=M)


# --- the five cases from the ticket ---------------------------------------


def test_three_failures_in_a_row_opens():
    assert decide_([FAIL, FAIL, FAIL]) == Action.OPEN


def test_two_failures_then_a_success_does_nothing():
    assert decide_([FAIL, FAIL, OK]) == Action.NOTHING


def test_open_incident_plus_two_successes_resolves():
    assert decide_([OK, OK], incident_open=True) == Action.RESOLVE


def test_open_incident_success_then_failure_stays_open():
    assert decide_([OK, FAIL], incident_open=True) == Action.NOTHING


def test_an_incident_never_opens_twice():
    assert decide_([FAIL, FAIL, FAIL], incident_open=True) == Action.NOTHING


# --- edge cases the rules imply -------------------------------------------


def test_fewer_results_than_threshold_does_nothing():
    assert decide_([FAIL, FAIL]) == Action.NOTHING


def test_a_blip_between_successes_does_nothing():
    assert decide_([OK, FAIL, OK]) == Action.NOTHING


def test_only_the_most_recent_results_count():
    assert decide_([FAIL, FAIL, FAIL, OK]) == Action.NOTHING


def test_no_results_does_nothing():
    assert decide_([]) == Action.NOTHING
    assert decide_([], incident_open=True) == Action.NOTHING


def test_thresholds_below_one_are_rejected():
    with pytest.raises(ValueError):
        decide([FAIL], False, fail_threshold=0, ok_threshold=2)
