"""Bounds on settings are enforced at startup, not buried in the code that uses them."""

import pytest
from django.core.checks import run_checks


def pulse_errors():
    return [e for e in run_checks() if e.id and e.id.startswith("pulse.")]


def test_defaults_pass():
    assert pulse_errors() == []


@pytest.mark.parametrize("value", [0, 11])
def test_out_of_range_fail_threshold_is_reported(settings, value):
    settings.PULSE_FAIL_THRESHOLD = value
    assert [e.id for e in pulse_errors()] == ["pulse.E001"]


@pytest.mark.parametrize("value", [-1, 0, 11])
def test_out_of_range_alert_attempts_is_reported(settings, value):
    settings.PULSE_ALERT_MAX_ATTEMPTS = value
    assert [e.id for e in pulse_errors()] == ["pulse.E002"]


@pytest.mark.parametrize("value", [1, 10])
def test_alert_attempts_bounds_are_inclusive(settings, value):
    settings.PULSE_ALERT_MAX_ATTEMPTS = value
    assert pulse_errors() == []


@pytest.mark.parametrize("value", [-1, 301])
def test_out_of_range_status_cache_seconds_is_reported(settings, value):
    settings.PULSE_STATUS_CACHE_SECONDS = value
    assert [e.id for e in pulse_errors()] == ["pulse.E003"]


@pytest.mark.parametrize("value", [0, 300])
def test_status_cache_seconds_bounds_are_inclusive(settings, value):
    settings.PULSE_STATUS_CACHE_SECONDS = value
    assert pulse_errors() == []


@pytest.mark.parametrize("value", [0, 31])
def test_out_of_range_webhook_timeout_is_reported(settings, value):
    settings.PULSE_WEBHOOK_TIMEOUT_S = value
    assert [e.id for e in pulse_errors()] == ["pulse.E004"]


def test_every_bad_setting_gets_its_own_error(settings):
    settings.PULSE_FAIL_THRESHOLD = 0
    settings.PULSE_OK_THRESHOLD = 99
    settings.PULSE_ALERT_MAX_ATTEMPTS = 0
    assert sorted(e.id for e in pulse_errors()) == [
        "pulse.E001",
        "pulse.E001",
        "pulse.E002",
    ]


@pytest.mark.parametrize("value", [59, 86_401])
def test_out_of_range_token_lifetime_is_reported(settings, value):
    settings.PULSE_JWT_TTL_SECONDS = value
    assert [e.id for e in pulse_errors()] == ["pulse.E005"]


@pytest.mark.parametrize("value", [60, 86_400])
def test_token_lifetime_bounds_are_inclusive(settings, value):
    settings.PULSE_JWT_TTL_SECONDS = value
    assert pulse_errors() == []


def test_a_short_token_secret_is_reported_without_printing_it(settings):
    settings.PULSE_JWT_SECRET = "s3cret-but-far-too-short"

    (error,) = pulse_errors()

    assert error.id == "pulse.E006"
    assert settings.PULSE_JWT_SECRET not in f"{error.msg} {error.hint}"


def test_a_token_secret_of_32_characters_is_long_enough(settings):
    settings.PULSE_JWT_SECRET = "x" * 32
    assert pulse_errors() == []
