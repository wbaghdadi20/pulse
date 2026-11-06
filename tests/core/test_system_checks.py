"""The threshold bounds are enforced at startup, not buried in decide()."""

import pytest
from django.core.checks import run_checks


def pulse_errors():
    return [e for e in run_checks() if e.id.startswith("pulse.")]


def test_defaults_pass():
    assert pulse_errors() == []


@pytest.mark.parametrize("value", [0, 11])
def test_out_of_range_fail_threshold_is_reported(settings, value):
    settings.PULSE_FAIL_THRESHOLD = value
    assert [e.id for e in pulse_errors()] == ["pulse.E001"]


def test_both_out_of_range_gives_two_errors(settings):
    settings.PULSE_FAIL_THRESHOLD = 0
    settings.PULSE_OK_THRESHOLD = 99
    assert len(pulse_errors()) == 2
