"""Property-based tests for the incident state machine (D4 Extra).

test_incidents.py pins a handful of hand-picked sequences. Here Hypothesis
generates thousands of random up/down sequences and thresholds instead, and
checks rules that must hold for every one of them. When a rule breaks,
Hypothesis shrinks the input to the smallest sequence that still breaks it.
How many examples run is set by the profile in tests/conftest.py.
"""

from hypothesis import event, given
from hypothesis import strategies as st

from monitors.incidents import Action, decide

OK, FAIL = True, False

# "Strategies" describe the shape of the input; Hypothesis picks the values.
# Thresholds stay small relative to the sequence so OPEN and RESOLVE actually
# happen; --hypothesis-show-statistics shows how often (see event() below).
thresholds = st.integers(min_value=1, max_value=5)
sequences = st.lists(st.booleans(), max_size=60)


def replay(results, *, fail_threshold, ok_threshold):
    """Feed results one check at a time, the way check_monitor() does in production.

    Yields (history so far, incident_open before the decision, action), then
    applies the action to the state exactly as evaluate_incidents() would.
    """
    incident_open = False
    for i in range(1, len(results) + 1):
        history = results[:i]
        action = decide(
            history,
            incident_open,
            fail_threshold=fail_threshold,
            ok_threshold=ok_threshold,
        )
        event(action.value)  # counted in the statistics: proves each branch ran
        yield history, incident_open, action
        if action is Action.OPEN:
            incident_open = True
        elif action is Action.RESOLVE:
            incident_open = False


@given(results=sequences, n=thresholds, m=thresholds)
def test_never_opens_a_second_incident(results, n, m):
    for _, incident_open, action in replay(results, fail_threshold=n, ok_threshold=m):
        if action is Action.OPEN:
            assert not incident_open, "opened while one was already open"


@given(results=sequences, n=thresholds, m=thresholds)
def test_never_resolves_an_incident_that_is_not_open(results, n, m):
    for _, incident_open, action in replay(results, fail_threshold=n, ok_threshold=m):
        if action is Action.RESOLVE:
            assert incident_open, "resolved with nothing open"


@given(results=sequences, n=thresholds, m=thresholds)
def test_every_action_has_the_streak_behind_it(results, n, m):
    """OPEN only after N failures in a row, RESOLVE only after M successes in a row."""
    for history, _, action in replay(results, fail_threshold=n, ok_threshold=m):
        if action is Action.OPEN:
            assert history[-n:] == [FAIL] * n
        elif action is Action.RESOLVE:
            assert history[-m:] == [OK] * m


@given(results=sequences, n=thresholds, m=thresholds, incident_open=st.booleans())
def test_only_the_last_max_n_m_results_matter(results, n, m, incident_open):
    """evaluate_incidents() loads only the last max(N, M) checks (a LIMIT query).

    This proves that cut is safe: dropping older results never changes the answer.
    """
    window = results[-max(n, m) :]
    assert decide(window, incident_open, fail_threshold=n, ok_threshold=m) == decide(
        results, incident_open, fail_threshold=n, ok_threshold=m
    )
