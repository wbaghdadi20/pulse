from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from orgs.models import NAME_MAX_LENGTH as MAX_ORGANIZATION_NAME_LENGTH
from orgs.models import Membership
from orgs.serializers import MembershipSerializer
from orgs.slugs import MAX_LENGTH as MAX_SLUG_LENGTH
from orgs.slugs import validate_slug

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
    """An account and the organization it owns, created together."""

    organization_name = serializers.CharField(max_length=MAX_ORGANIZATION_NAME_LENGTH)
    # In every URL of the organization, and its schema's name: never changes.
    slug = serializers.CharField(
        max_length=MAX_SLUG_LENGTH, trim_whitespace=False, validators=[validate_slug]
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        try:
            # AUTH_PASSWORD_VALIDATORS; the user lets it reject "password = my email".
            validate_password(attrs["password"], user=User(email=attrs["email"]))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs


class SignedUpSerializer(serializers.ModelSerializer[Membership]):
    """What /signup answers: the account, and the organization it now owns."""

    id = serializers.IntegerField(source="user.id")
    email = serializers.EmailField(source="user.email")
    created_at = serializers.DateTimeField(source="user.created_at")
    organization = MembershipSerializer(source="*")

    class Meta:
        model = Membership
        fields = ["id", "email", "created_at", "organization"]
