from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import User

# Hashing cost grows with the input, so a password has a ceiling as well as a floor.
MAX_PASSWORD_LENGTH = 128


class CredentialsSerializer(serializers.Serializer[Any]):
    """What /login accepts. No password rules here: they would describe the policy."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True, max_length=MAX_PASSWORD_LENGTH, trim_whitespace=False
    )


class SignupSerializer(CredentialsSerializer):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        try:
            # AUTH_PASSWORD_VALIDATORS; the user lets it reject "password = my email".
            validate_password(attrs["password"], user=User(email=attrs["email"]))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs


class UserSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ["id", "email", "created_at"]
