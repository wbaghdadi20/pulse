"""Every endpoint needs a valid token unless it is on the public list below."""

from datetime import timedelta

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import tokens
from accounts.models import User
from tests.conftest import client_for

pytestmark = pytest.mark.django_db

PUBLIC = {"health", "status-page", "signup", "login"}
METHODS = ["get", "post", "put", "patch", "delete"]


def routes(patterns=None) -> dict[str, str]:
    """{route name: a concrete path} for every route in the project."""
    found: dict[str, str] = {}
    for entry in patterns or get_resolver().url_patterns:
        if isinstance(entry, URLResolver):
            found |= routes(entry.url_patterns)
        elif isinstance(entry, URLPattern) and entry.name:
            kwargs = dict.fromkeys(entry.pattern.converters, 1)
            found[entry.name] = reverse(entry.name, kwargs=kwargs)
    return found


def with_header(value: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=value)
    return client


@pytest.mark.parametrize("method", METHODS)
def test_every_route_that_is_not_public_answers_401_without_a_token(anon, method):
    protected = {name: path for name, path in routes().items() if name not in PUBLIC}
    assert protected, "the route walk found nothing"

    answers = {name: getattr(anon, method)(path) for name, path in protected.items()}

    assert {name: r.status_code for name, r in answers.items()} == dict.fromkeys(
        protected, 401
    )


def test_the_public_routes_are_exactly_the_listed_ones(anon):
    open_routes = {
        name
        for name, path in routes().items()
        if all(getattr(anon, method)(path).status_code != 401 for method in METHODS)
    }

    assert open_routes == PUBLIC


def test_a_401_says_which_scheme_to_use(anon):
    response = anon.get("/monitors/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_valid_token_is_accepted(user):
    assert client_for(user).get("/monitors/").status_code == 200


def test_the_scheme_is_matched_whatever_its_case(user):
    client = with_header(f"bearer {tokens.issue(user.pk)}")

    assert client.get("/monitors/").status_code == 200


def test_an_expired_token_is_rejected(user, settings):
    issued = timezone.now() - timedelta(seconds=settings.PULSE_JWT_TTL_SECONDS + 1)
    client = with_header(f"Bearer {tokens.issue(user.pk, now=issued)}")

    assert client.get("/monitors/").status_code == 401


def test_the_token_of_a_deleted_account_is_rejected(user):
    client = client_for(user)
    User.objects.filter(pk=user.pk).delete()

    assert client.get("/monitors/").status_code == 401


@pytest.mark.parametrize(
    "header",
    ["Bearer", "Bearer not-a-token", "Bearer two parts", "Bearer \xff"],
    ids=["no token", "garbage", "too many parts", "not text"],
)
def test_a_malformed_bearer_header_is_rejected(header):
    response = with_header(header).get("/monitors/")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token."}


def test_another_scheme_counts_as_no_token():
    response = with_header("Basic d2FsaWQ6c2VjcmV0").get("/monitors/")

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


@pytest.mark.parametrize("path", ["/health", "/status"])
def test_a_public_page_ignores_a_bad_token(path):
    assert with_header("Bearer not-a-token").get(path).status_code == 200
