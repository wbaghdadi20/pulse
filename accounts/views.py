from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from core.throttling import AuthRateThrottle
from core.views import PublicAPIView

from . import services
from .authentication import KEYWORD
from .serializers import CredentialsSerializer, SignupSerializer, UserSerializer


class SignupView(PublicAPIView):
    throttle_classes = [AuthRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.signup(**serializer.validated_data)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(PublicAPIView):
    throttle_classes = [AuthRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = CredentialsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = services.login(**serializer.validated_data)
        return Response(
            {
                "access_token": token,
                "token_type": KEYWORD,
                "expires_in": settings.PULSE_JWT_TTL_SECONDS,
            }
        )
