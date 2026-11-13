"""The request id: where it comes from, where it goes, and what the request line says."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from hypothesis import given
from hypothesis import strategies as st

from accounts import tokens
from core.logs import ContextFilter, request_id
from core.middleware import ACCEPTABLE, id_for
from tests.core.test_logs import json_formatter

# --- choosing the id ----------------------------------------------------------------


def test_without_a_header_the_id_is_a_new_uuid4():
    first, second = id_for(None), id_for(None)

    assert uuid.UUID(first).version == 4
    assert first != second


@pytest.mark.parametrize(
    "sent", ["abc-123", "0af7651916cd43dd8448eb211c80319c", "req_1.2", "a" * 64]
)
def test_an_acceptable_id_from_the_caller_is_kept(sent):
    assert id_for(sent) == sent


@pytest.mark.parametrize(
    "sent",
    [
        "",
        "a" * 65,
        "two words",
        "line\nbreak",
        "tab\there",
        '"quoted"',
        "<script>",
        "café",
        "١٢٣",  # digits, but not ASCII ones
    ],
)
def test_anything_else_is_replaced_by_a_new_id(sent):
    chosen = id_for(sent)

    assert chosen != sent
    assert uuid.UUID(chosen).version == 4


@given(st.one_of(st.none(), st.text()))
def test_whatever_is_sent_the_id_is_short_and_plain(sent):
    assert ACCEPTABLE.fullmatch(id_for(sent))


# --- through the API ----------------------------------------------------------------


def test_the_response_carries_the_id(anon):
    response = anon.get("/health")

    assert uuid.UUID(response.headers["X-Request-ID"]).version == 4


def test_the_callers_id_comes_back(anon):
    response = anon.get("/health", HTTP_X_REQUEST_ID="from-the-gateway-42")

    assert response.headers["X-Request-ID"] == "from-the-gateway-42"


def test_an_unacceptable_id_is_not_sent_back(anon):
    response = anon.get("/health", HTTP_X_REQUEST_ID="two words")

    assert response.headers["X-Request-ID"] != "two words"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("get", "/monitors/", 401),
        ("get", "/no-such-page", 404),
        ("post", "/health", 405),
        ("get", "/monitors", 301),
    ],
)
def test_an_error_or_a_redirect_carries_it_too(anon, method, path, status):
    response = getattr(anon, method)(path, HTTP_X_REQUEST_ID="abc-123")

    assert response.status_code == status
    assert response.headers["X-Request-ID"] == "abc-123"


def test_the_id_is_gone_once_the_request_is_answered(anon):
    anon.get("/health", HTTP_X_REQUEST_ID="abc-123")

    assert request_id.get() is None


def test_it_is_the_first_middleware():
    assert settings.MIDDLEWARE[0] == "core.middleware.RequestIDMiddleware"


# --- in the logs --------------------------------------------------------------------


@pytest.fixture
def logged(caplog) -> Callable[[], list[dict[str, Any]]]:
    """Call it after the requests: every record so far, as the real handler would
    write it. The filter added here is the one that handler has."""
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(ContextFilter())
    formatter = json_formatter()
    return lambda: [json.loads(formatter.format(record)) for record in caplog.records]


@pytest.mark.django_db
def test_every_line_of_one_request_has_the_same_id(anon, logged):
    """Django's own line for a 401 is written after every middleware has returned."""
    response = anon.get("/monitors/")

    lines = logged()
    assert [line["logger"] for line in lines] == ["core.middleware", "django.request"]
    assert {line["request_id"] for line in lines} == {response.headers["X-Request-ID"]}


def test_two_requests_do_not_share_an_id(anon, logged):
    anon.get("/health")
    anon.get("/health")

    first, second = (line["request_id"] for line in logged())
    assert first != second


def test_the_request_line_says_what_was_asked_and_how_it_went(anon, logged):
    anon.get("/health")

    (line,) = logged()
    assert line["message"] == "request finished"
    assert (line["method"], line["path"], line["status"]) == ("GET", "/health", 200)
    assert isinstance(line["duration_ms"], int) and line["duration_ms"] >= 0


@pytest.mark.django_db
def test_no_password_token_or_query_string_reaches_the_logs(anon, user, logged):
    token = tokens.issue(user.pk)

    anon.post(
        "/login?next=SECRET-IN-QUERY",
        {"email": "walid@example.com", "password": "SECRET-PASSWORD"},
    )
    anon.get("/monitors/?after=SECRET-IN-QUERY", HTTP_AUTHORIZATION=f"Bearer {token}")

    text = json.dumps(logged())
    assert text.count("request finished") == 2
    assert "SECRET-PASSWORD" not in text
    assert "SECRET-IN-QUERY" not in text
    assert token not in text
