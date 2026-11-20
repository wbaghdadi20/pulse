"""Organizations are the tenants: each row names a Postgres schema. These tables are
shared, in public: the request needs them before it can enter a schema."""

from typing import Any

from django.db import models
from django_tenants.models import DomainMixin, TenantMixin

from accounts.models import User

from .errors import SlugChanged
from .slugs import MAX_LENGTH, validate_slug

NAME_MAX_LENGTH = 100


class Organization(TenantMixin):  # type: ignore[misc]
    # TenantMixin's field, its unique constraint named here instead (ADR 0003).
    schema_name = models.CharField(max_length=63)
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    slug = models.CharField(max_length=MAX_LENGTH, validators=[validate_slug])
    created_at = models.DateTimeField(auto_now_add=True)

    # Saving the row does not create its schema: that is a step of its own.
    auto_create_schema = False
    # The template holds no rows worth copying; NODATA makes sure none are.
    clone_mode = "NODATA"

    class Meta:
        db_table = "organizations"
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="uq_organization_slug"),
            models.UniqueConstraint(
                fields=["schema_name"], name="uq_organization_schema_name"
            ),
        ]

    def __str__(self) -> str:
        return str(self.slug)

    @classmethod
    def from_db(cls, *args: Any, **kwargs: Any) -> "Organization":
        org: Organization = super().from_db(*args, **kwargs)
        org._saved_slug = org.slug
        return org

    def save(self, *args: Any, **kwargs: Any) -> None:
        saved = getattr(self, "_saved_slug", None)
        if saved is not None and saved != self.slug:
            raise SlugChanged(f"{saved} cannot become {self.slug}")
        super().save(*args, **kwargs)
        self._saved_slug = self.slug


class Domain(DomainMixin):  # type: ignore[misc]
    """django-tenants' way from a URL to a tenant. In subfolder mode `domain` holds
    the slug, written once at signup."""

    class Meta:
        db_table = "organization_domains"


class MembershipQuerySet(models.QuerySet["Membership"]):
    def active(self) -> "MembershipQuerySet":
        """Memberships that still give access: their account exists. Ticket 3 adds
        removal, and with it `removed_at IS NULL`."""
        return self.filter(user__isnull=False)


class Membership(models.Model):
    """A person in an organization, with a role there."""

    class Role(models.TextChoices):
        OWNER = "owner"

    # Nullable, SET_NULL: the membership, and what tenant rows say about it, outlive
    # a purged account.
    user = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="memberships",
        db_index=False,
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MembershipQuerySet.as_manager()

    class Meta:
        db_table = "memberships"
        constraints = [
            # One role per person per organization. No index of its own on user:
            # this one starts with user_id.
            models.UniqueConstraint(
                fields=["user", "organization"], name="uq_membership_user_organization"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.organization_id} as {self.role}"
