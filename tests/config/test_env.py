"""Each env parsing rule, tested as a pure function with a dict in place of os.environ.

Hypothesis generates the inputs. The tests never re-implement the parser to compute
the expected answer (that would just be the same code twice); they check round trips
(build a string from a value, read it back, get the value) and feed inputs that are
invalid by construction (letters where a number must be) and expect a refusal.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from config.env import (
    FALSE_WORDS,
    TRUE_WORDS,
    EnvError,
    env_bool,
    env_int,
    env_list,
    env_str,
)

# --- strategies -------------------------------------------------------------------

# Up to three spaces or tabs: what a hand-edited .env line realistically carries.
padding = st.text(alphabet=" \t", max_size=3)

# Hostname-ish items: never a comma (the separator) and never blank.
names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-", min_size=1)

# Letters and punctuation only: a string that cannot be an integer, by construction.
not_a_number = st.text(alphabet=st.characters(categories=["L", "P"]), min_size=1)


def spellings(words):
    """Each accepted word in lower, UPPER and Title case, with padding around it."""
    word = st.sampled_from(sorted(words)).flatmap(
        lambda w: st.sampled_from([w, w.upper(), w.title()])
    )
    return st.builds(
        lambda before, w, after: before + w + after, padding, word, padding
    )


# --- env_str ----------------------------------------------------------------------


def test_str_returns_the_value():
    assert env_str("X", environ={"X": "hello"}) == "hello"


def test_str_falls_back_to_the_default():
    assert env_str("X", default="d", environ={}) == "d"


def test_str_missing_without_default_names_the_variable():
    with pytest.raises(EnvError, match="DJANGO_SECRET_KEY"):
        env_str("DJANGO_SECRET_KEY", environ={})


def test_str_empty_counts_as_unset():
    assert env_str("X", default="d", environ={"X": ""}) == "d"


@given(st.text(alphabet=" \t", min_size=1))
def test_str_blank_counts_as_unset(value):
    assert env_str("X", default="d", environ={"X": value}) == "d"


# --- env_bool ---------------------------------------------------------------------


@given(spellings(TRUE_WORDS))
def test_bool_true_spellings(value):
    assert env_bool("X", environ={"X": value}) is True


@given(spellings(FALSE_WORDS))
def test_bool_false_spellings(value):
    assert env_bool("X", environ={"X": value}) is False


@given(
    st.text().filter(
        lambda s: s.strip() != "" and s.strip().lower() not in TRUE_WORDS | FALSE_WORDS
    )
)
def test_bool_refuses_anything_else(value):
    """The old code mapped "yes" and "True " to False silently. Refuse instead."""
    with pytest.raises(EnvError, match="X"):
        env_bool("X", environ={"X": value})


def test_bool_unset_uses_the_default():
    assert env_bool("X", default=False, environ={}) is False


# --- env_int ----------------------------------------------------------------------


@given(st.integers())
def test_int_round_trips_every_integer(n):
    assert env_int("X", environ={"X": str(n)}) == n


@given(st.integers(), padding, padding)
def test_int_ignores_padding(n, before, after):
    assert env_int("X", environ={"X": before + str(n) + after}) == n


@given(not_a_number)
def test_int_refuses_non_numbers(value):
    with pytest.raises(EnvError, match="X"):
        env_int("X", environ={"X": value})


@pytest.mark.parametrize("value", ["3.0", "3,0", "three"])
def test_int_refuses_almost_numbers(value):
    with pytest.raises(EnvError, match="X"):
        env_int("X", environ={"X": value})


def test_int_unset_uses_the_default():
    assert env_int("X", default=3, environ={}) == 3


# --- env_list ---------------------------------------------------------------------


@given(st.lists(names, min_size=1))
def test_list_round_trips_any_list(items):
    assert env_list("X", environ={"X": ",".join(items)}) == items


@given(st.lists(st.tuples(padding, names, padding), min_size=1))
def test_list_strips_padding_around_items(padded):
    joined = ",".join(before + item + after for before, item, after in padded)
    assert env_list("X", environ={"X": joined}) == [item for _, item, _ in padded]


def test_list_drops_empty_entries():
    assert env_list("X", environ={"X": "a,,b,"}) == ["a", "b"]


def test_list_unset_uses_the_default():
    assert env_list("X", default=[], environ={}) == []


def test_list_returns_a_copy_of_the_default():
    default: list[str] = []
    env_list("X", default=default, environ={}).append("leaked")
    assert default == []
