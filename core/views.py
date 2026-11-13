from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from . import readiness


class PublicAPIView(APIView):
    """An endpoint that needs no login. Everything else requires a token by default.

    No authentication classes either: a stale token sent along is not read, so it
    cannot turn a public page into a 401. Not rate limited unless the view asks:
    /health is polled by machines, and a 429 there would read as "Pulse is down".
    """

    authentication_classes: list[type[BaseAuthentication]] = []
    permission_classes = [AllowAny]
    throttle_classes: list[type[BaseThrottle]] = []


class HealthView(PublicAPIView):
    """Liveness: the process answers. It asks nothing of Postgres or Redis, so a
    database blip does not make a healthy process look dead."""

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class ReadyView(PublicAPIView):
    """Readiness: the process answers and its dependencies do too. 503 names the
    ones that failed."""

    def get(self, request: Request) -> Response:
        answers = readiness.answers()
        ready = all(answers.values())
        return Response(
            {
                "status": "ok" if ready else "unavailable",
                "checks": {
                    name: "ok" if answered else "failed"
                    for name, answered in answers.items()
                },
            },
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
