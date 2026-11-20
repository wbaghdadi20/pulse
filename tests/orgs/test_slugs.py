"""A slug names an organization's URLs and its schema, so only safe ones get through."""

import pytest
from django.core.exceptions import ValidationError

from orgs import slugs

HOSTILE = [
    'x"; DROP SCHEMA public CASCADE; --',
    "acme'--",
    "acme/../globex",
    "acme;",
    "ACME",
    "Acme",
    "acme_corp",
    "-acme",
    "acme-",
    "ac--me",
    "1acme",
    "acmé",
    "ac me",
    "acme\x00",
    "acme\n",
    "ab",
    "a" * 41,
    "",
]


@pytest.mark.parametrize(
    "slug", ["acme", "globex", "acme-corp", "a1b2", "abc", "a" * 40, "x-1-y"]
)
def test_lowercase_letters_digits_and_single_hyphens_are_a_slug(slug):
    assert slugs.problem(slug) is None
    slugs.validate_slug(slug)


@pytest.mark.parametrize("slug", HOSTILE)
def test_anything_else_is_refused(slug):
    assert slugs.problem(slug) is not None
    with pytest.raises(ValidationError):
        slugs.validate_slug(slug)
    with pytest.raises(ValueError):
        slugs.schema_name(slug)


@pytest.mark.parametrize("slug", sorted(slugs.RESERVED))
def test_reserved_slugs_are_refused(slug):
    assert slugs.problem(slug) == "This slug is reserved."


def test_the_schema_is_the_slug_with_a_prefix_and_underscores():
    assert slugs.schema_name("acme-corp") == "org_acme_corp"


def test_no_slug_names_public_or_another_non_organization_schema():
    """Every schema name a slug can give starts with org_ and fits Postgres's 63 bytes."""
    longest = slugs.schema_name("a" * slugs.MAX_LENGTH)
    assert longest.startswith("org_")
    assert len(longest.encode()) <= 63
