"""POST /signup: an account is created once, and its password is never kept or shown."""

import pytest
from django.db import IntegrityError, transaction

from accounts.models import User

pytestmark = pytest.mark.django_db

CREDENTIALS = {"email": "walid@example.com", "password": "correct horse battery"}


def test_signup_creates_the_account_and_returns_it_without_the_password(anon):
    response = anon.post("/signup", CREDENTIALS, format="json")

    assert response.status_code == 201
    user = User.objects.get()
    assert response.json() == {
        "id": user.pk,
        "email": "walid@example.com",
        "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
    }


def test_the_password_is_stored_as_an_argon2_hash(anon):
    anon.post("/signup", CREDENTIALS, format="json")

    stored = User.objects.get().password
    assert stored.startswith("argon2$argon2id$")
    assert CREDENTIALS["password"] not in stored


def test_the_email_is_stored_in_lowercase(anon):
    anon.post("/signup", {**CREDENTIALS, "email": "Walid@Example.COM"}, format="json")

    assert User.objects.get().email == "walid@example.com"


@pytest.mark.parametrize("email", ["walid@example.com", "WALID@example.com"])
def test_an_email_can_sign_up_once_whatever_its_case(anon, email):
    anon.post("/signup", CREDENTIALS, format="json")

    response = anon.post("/signup", {**CREDENTIALS, "email": email}, format="json")

    assert response.status_code == 409
    assert User.objects.count() == 1


@pytest.mark.parametrize(
    "password",
    ["short1!", "password", "1234567890", "walid@example.com", "x" * 129],
    ids=["too short", "too common", "only digits", "same as the email", "too long"],
)
def test_a_weak_password_is_rejected(anon, password):
    response = anon.post(
        "/signup", {**CREDENTIALS, "password": password}, format="json"
    )

    assert response.status_code == 400
    assert set(response.json()) == {"password"}
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "body, field",
    [
        ({"password": CREDENTIALS["password"]}, "email"),
        ({"email": CREDENTIALS["email"]}, "password"),
        ({**CREDENTIALS, "email": "not-an-email"}, "email"),
    ],
)
def test_a_missing_or_malformed_field_is_a_400_on_that_field(anon, body, field):
    response = anon.post("/signup", body, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {field}


def test_the_database_refuses_an_email_that_is_not_lowercase():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="Walid@example.com", password="!")])
