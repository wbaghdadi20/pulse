from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView


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
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})
