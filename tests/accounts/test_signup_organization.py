"""Signing up creates the account, its organization and the account's owner
membership there, all in one transaction (TEN-1, docs/adr/0026)."""

import pytest
from django_tenants.clone import CloneSchema
from django_tenants.utils import schema_exists

from accounts import services as accounts
from accounts.models import User
from orgs import services
from orgs.errors import InvalidSlug, TemplateNotReady
from orgs.models import Membership, Organization
from tests.orgs.test_slugs import HOSTILE

pytestmark = pytest.mark.django_db

SIGNUP = {
    "email": "walid@example.com",
    "password": "correct horse battery",
    "organization_name": "Initech",
    "slug": "initech",
}


def nothing_was_created(slug: str = "initech") -> bool:
    return (
        not User.objects.filter(email=SIGNUP["email"]).exists()
        and not Organization.objects.filter(slug=slug).exists()
        and not schema_exists(f"org_{slug}")
    )


def test_signing_up_creates_an_organization_with_me_as_its_owner(anon):
    response = anon.post("/signup", SIGNUP, format="json")

    assert response.status_code == 201
    membership = Membership.objects.select_related("organization").get(
        user__email=SIGNUP["email"]
    )
    assert (membership.role, membership.organization.slug) == ("owner", "initech")
    assert schema_exists("org_initech")


def test_the_new_owner_can_use_their_organization_straight_away(anon):
    anon.post("/signup", SIGNUP, format="json")
    login = anon.post(
        "/login", {k: SIGNUP[k] for k in ("email", "password")}, format="json"
    )
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access_token']}")

    created = anon.post(
        "/t/initech/monitors/",
        {"name": "web", "url": "http://target:8000/", "timeout_s": 5, "interval_s": 30},
        format="json",
    )

    assert created.status_code == 201
    assert anon.get("/t/initech/monitors/").json()["count"] == 1


@pytest.mark.parametrize("slug", ["acme", "globex"])
def test_a_taken_slug_is_a_409_and_creates_no_account(anon, slug):
    response = anon.post("/signup", {**SIGNUP, "slug": slug}, format="json")

    assert response.status_code == 409
    assert response.json() == {"detail": f'The slug "{slug}" is taken.'}
    assert not User.objects.filter(email=SIGNUP["email"]).exists()


def test_the_schema_failing_to_be_created_leaves_no_account(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("clone failed")

    monkeypatch.setattr(CloneSchema, "clone_schema", broken)

    with pytest.raises(RuntimeError, match="clone failed"):
        accounts.signup(**SIGNUP)

    assert nothing_was_created()


@pytest.mark.parametrize(
    "step", ["restore_constraint_names", "add_member"], ids=["renames", "membership"]
)
def test_a_failure_after_the_clone_leaves_no_account_and_no_schema(monkeypatch, step):
    real = getattr(services, step)

    def fails_afterwards(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError(f"{step} failed")

    monkeypatch.setattr(services, step, fails_afterwards)

    with pytest.raises(RuntimeError, match="failed"):
        accounts.signup(**SIGNUP)

    assert nothing_was_created()


def test_a_template_that_does_not_match_the_code_creates_no_account(monkeypatch):
    on_disk = services._tenant_migrations()
    monkeypatch.setattr(
        services, "_tenant_migrations", lambda: on_disk | {("monitors", "9999_x")}
    )

    with pytest.raises(TemplateNotReady):
        accounts.signup(**SIGNUP)

    assert nothing_was_created()


def test_a_signup_waiting_on_a_template_migration_answers_503(
    anon, template_being_migrated
):
    response = anon.post("/signup", SIGNUP, format="json")

    assert response.status_code == 503
    assert nothing_was_created()


@pytest.mark.parametrize("slug", HOSTILE)
def test_a_hostile_slug_is_refused_before_any_sql(
    anon, slug, django_assert_num_queries
):
    with django_assert_num_queries(0):
        with pytest.raises(InvalidSlug):
            accounts.signup(**{**SIGNUP, "slug": slug})
        response = anon.post("/signup", {**SIGNUP, "slug": slug}, format="json")

    assert response.status_code == 400
    assert set(response.json()) == {"slug"}
