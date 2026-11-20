"""Domain errors become HTTP answers in one place: core/exception_handler.py."""

from core.errors import InvalidInputError, UnavailableError
from core.exception_handler import exception_handler


def test_invalid_input_is_a_400_on_its_field():
    response = exception_handler(InvalidInputError("Too short.", field="slug"), {})

    assert response is not None
    assert (response.status_code, response.data) == (400, {"slug": ["Too short."]})


def test_invalid_input_about_no_field_in_particular_is_a_400_on_detail():
    response = exception_handler(InvalidInputError("Not like that."), {})

    assert response is not None
    assert (response.status_code, response.data) == (
        400,
        {"detail": ["Not like that."]},
    )


def test_unavailable_is_a_503_that_says_when_to_try_again():
    response = exception_handler(UnavailableError("Try again shortly.", 30), {})

    assert response is not None
    assert response.status_code == 503
    assert response.data == {"detail": "Try again shortly."}
    assert response.headers["Retry-After"] == "30"
