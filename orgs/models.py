"""Organizations are the tenants: each row names a Postgres schema. Shared, in public."""

from django.db import models
from django_tenants.models import DomainMixin, TenantMixin

from accounts.models import User


class Organization(TenantMixin):  # type: ignore[misc]
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Creating the row creates (and migrates or clones) its schema.
    auto_create_schema = True

    class Meta:
        db_table = "organizations"

    def __str__(self) -> str:
        return self.slug


class Domain(DomainMixin):  # type: ignore[misc]
    """Subfolder mode still looks tenants up here: `domain` holds the slug."""

    class Meta:
        db_table = "organization_domains"


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OWNER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="uq_membership_user_org"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.organization_id} as {self.role}"
