"""Organization slugs. A slug is in every tenant URL (/t/<slug>/) and names the
organization's schema, so it is checked before it gets anywhere near SQL."""

import re

from django.core.exceptions import ValidationError

MIN_LENGTH = 3
MAX_LENGTH = 40
# Django's slug characters, narrowed: lowercase, no underscore, no leading, trailing
# or double hyphen. Without "_", turning "-" into "_" cannot give two slugs one schema.
PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# The public tenant's row has this slug. Every other schema name is out of reach
# anyway: an organization's always starts with org_. The rest would pass for Pulse.
RESERVED = frozenset({"public", "admin", "api", "pulse", "status", "www"})
SCHEMA_PREFIX = "org_"


def problem(slug: str) -> str | None:
    """What is wrong with this slug, or None if it can be used."""
    if not MIN_LENGTH <= len(slug) <= MAX_LENGTH:
        return f"Between {MIN_LENGTH} and {MAX_LENGTH} characters."
    if not PATTERN.fullmatch(slug):
        return "Lowercase letters, digits and single hyphens, starting with a letter."
    if slug in RESERVED:
        return "This slug is reserved."
    return None


def validate_slug(value: str) -> None:
    """The same rules as a Django validator, for the model field and the serializer."""
    message = problem(value)
    if message is not None:
        raise ValidationError(message, code="invalid_slug")


def schema_name(slug: str) -> str:
    """org_<slug> with "_" for "-": never public, the template or a Postgres schema."""
    if problem(slug) is not None:
        raise ValueError(f"not a valid slug: {slug!r}")
    return SCHEMA_PREFIX + slug.replace("-", "_")
