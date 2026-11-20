from core.errors import ConflictError, InvalidInputError, UnavailableError


class SlugTaken(ConflictError):
    def __init__(self, slug: str) -> None:
        super().__init__(f'The slug "{slug}" is taken.')
        self.slug = slug


class InvalidSlug(InvalidInputError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem, field="slug")


class SlugChanged(Exception):
    """URLs and the schema's name are built from the slug, so it never changes."""


class NotAnActiveMember(InvalidInputError):
    """Tenant rows point at memberships with no foreign key across schemas, so this
    check is what keeps a dangling or foreign membership out (docs/adr/0026)."""

    def __init__(self, membership_id: int) -> None:
        super().__init__(
            f"Membership {membership_id} is not an active member of this organization.",
            field="assignee",
        )
        self.membership_id = membership_id


class OrganizationsBusy(UnavailableError):
    """Another organization is being created, or the template migrated, for too long."""

    def __init__(self) -> None:
        super().__init__(
            "Organizations cannot be created right now. Try again shortly."
        )


class TemplateNotReady(Exception):
    """Not a domain error: the template schema is missing, behind or ahead of the
    code. `make migrate`, with the code that is deployed."""
