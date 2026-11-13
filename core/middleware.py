"""Gives every request an id, and logs one line when the request has been answered."""

import logging
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from django.core.signals import request_finished
from django.http import HttpRequest, HttpResponse

from .logs import request_id

log = logging.getLogger(__name__)

HEADER = "X-Request-ID"
# What a caller may send as its own id. The value goes into every log line and
# back out in a response header, so anything else is replaced, not cleaned up.
ACCEPTABLE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def id_for(sent: str | None) -> str:
    """The caller's id if it sent an acceptable one, otherwise a new uuid4."""
    if sent is not None and ACCEPTABLE.fullmatch(sent):
        return sent
    return str(uuid.uuid4())


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        current = id_for(request.headers.get(HEADER))
        request_id.set(current)
        started = time.perf_counter()
        response = self.get_response(request)
        response[HEADER] = current
        # The path without its query string, and nothing from the headers or the
        # body: those are where tokens and passwords travel.
        log.info(
            "request finished",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return response


def forget_request_id(**kwargs: Any) -> None:
    """This process serves the next request too. Not done when the middleware
    returns: Django logs its own line for a 4xx or 5xx after that."""
    request_id.set(None)


request_finished.connect(forget_request_id)
