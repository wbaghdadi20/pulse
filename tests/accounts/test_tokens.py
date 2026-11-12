"""Access tokens: what verify() accepts is exactly what issue() made, until it expires."""

from datetime import timedelta

import jwt
import pytest
from django.utils import timezone

from accounts import tokens


def forged(settings, **overrides) -> str:
    """A token signed with the real secret, so only the claims under test are wrong."""
    now = timezone.now()
    claims = {"sub": "7", "iat": now, "exp": now + timedelta(minutes=5), **overrides}
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, settings.PULSE_JWT_SECRET, algorithm=tokens.ALGORITHM)


def test_a_token_names_the_user_it_was_issued_for():
    assert tokens.verify(tokens.issue(7)) == 7


def test_a_token_expires_after_the_configured_lifetime(settings):
    settings.PULSE_JWT_TTL_SECONDS = 60
    long_ago = timezone.now() - timedelta(seconds=61)

    with pytest.raises(tokens.InvalidToken):
        tokens.verify(tokens.issue(7, now=long_ago))
    assert tokens.verify(tokens.issue(7, now=long_ago + timedelta(seconds=30))) == 7


def test_a_token_signed_with_another_secret_is_rejected(settings):
    token = tokens.issue(7)
    settings.PULSE_JWT_SECRET = "a-different-secret-of-at-least-32-characters"

    with pytest.raises(tokens.InvalidToken):
        tokens.verify(token)


def test_a_token_whose_payload_was_edited_is_rejected():
    header, payload, signature = tokens.issue(7).split(".")
    other_payload = tokens.issue(8).split(".")[1]

    with pytest.raises(tokens.InvalidToken):
        tokens.verify(f"{header}.{other_payload}.{signature}")


def test_an_unsigned_token_is_rejected():
    unsigned = jwt.encode({"sub": "7"}, key="", algorithm="none")

    with pytest.raises(tokens.InvalidToken):
        tokens.verify(unsigned)


def test_another_algorithm_is_rejected_even_with_the_right_secret(settings):
    now = timezone.now()
    token = jwt.encode(
        {"sub": "7", "exp": now + timedelta(minutes=5)},
        settings.PULSE_JWT_SECRET,
        algorithm="HS512",
    )

    with pytest.raises(tokens.InvalidToken):
        tokens.verify(token)


@pytest.mark.parametrize(
    "overrides",
    [{"exp": None}, {"sub": None}, {"sub": "not-a-number"}],
    ids=["no expiry", "no subject", "subject is not an id"],
)
def test_a_token_missing_a_required_claim_is_rejected(settings, overrides):
    with pytest.raises(tokens.InvalidToken):
        tokens.verify(forged(settings, **overrides))


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_is_rejected(garbage):
    with pytest.raises(tokens.InvalidToken):
        tokens.verify(garbage)
