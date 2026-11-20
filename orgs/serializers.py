from typing import Any

from rest_framework import serializers

from .models import Membership


class MembershipSerializer(serializers.ModelSerializer[Membership]):
    """An organization as one of its members sees it: which, and their role there."""

    slug = serializers.CharField(source="organization.slug")
    name = serializers.CharField(source="organization.name")

    class Meta:
        model = Membership
        fields = ["slug", "name", "role"]
        read_only_fields: list[Any] = fields
