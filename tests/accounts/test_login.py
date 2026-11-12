"""POST /login: the right password gets a token, and every failure looks the same."""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db

EMAIL = "walid@example.com"
PASSWORD = "correct horse battery"


@pytest.fixture
def account() -> User:
    return User.objects.create_user(EMAIL, PASSWORD)


def test_login_returns_a_token_that_opens_the_api(anon, account, settings):
    response = anon.post("/login", {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == settings.PULSE_JWT_TTL_SECONDS
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
    assert anon.get("/monitors/").status_code == 200


def test_the_email_is_matched_whatever_its_case(anon, account):
    response = anon.post("/login", {"email": "Walid@Example.com", "password": PASSWORD})

    assert response.status_code == 200


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(anon, account):
    wrong_password = anon.post("/login", {"email": EMAIL, "password": "nope-nope-nope"})
    unknown_email = anon.post(
        "/login", {"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert "access_token" not in wrong_password.json()


def test_an_account_without_a_usable_password_cannot_log_in(anon):
    User.objects.create_user(EMAIL)

    response = anon.post("/login", {"email": EMAIL, "password": ""})

    assert response.status_code == 400  # a blank password never reaches the check
    assert anon.post("/login", {"email": EMAIL, "password": "!"}).status_code == 401


def test_login_does_not_say_what_a_valid_password_looks_like(anon, account):
    response = anon.post("/login", {"email": EMAIL, "password": "1"})

    assert response.status_code == 401
