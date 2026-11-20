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
    """Organizations are created one at a time, and this one waited too long."""

    def __init__(self) -> None:
        super().__init__(
            "Organizations cannot be created right now. Try again shortly."
        )


class TemplateNotReady(UnavailableError):
    """The template schema is missing, behind or ahead of the code: a deploy in
    progress, or `make migrate` not run. `detail` says which, for the log only."""

    def __init__(self, detail: str) -> None:
        super().__init__("Signup is unavailable for a moment. Try again shortly.", 30)
        self.detail = detail
