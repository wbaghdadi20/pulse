from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction

from core.db import violated_constraint
from orgs import services as orgs
from orgs.models import Membership

from . import tokens
from .errors import EmailTaken, InvalidCredentials
from .models import User


def signup(email: str, password: str, organization_name: str, slug: str) -> Membership:
    """The account, its organization with the organization's schema, and the
    account's owner membership there: all of it, or none of it."""
    orgs.check_slug(slug)
    try:
        with transaction.atomic():
            user = User.objects.create_user(email, password)
            org = orgs.create_organization(organization_name, slug)
            return orgs.add_member(org, user, Membership.Role.OWNER)
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_user_email":
            raise EmailTaken from exc
        raise


def login(email: str, password: str) -> str:
    """A token for these credentials.

    authenticate() hashes a password even when the email is unknown, so the two
    failures take the same time as well as giving the same answer.
    """
    user = authenticate(email=User.objects.normalize_email(email), password=password)
    if user is None:
        raise InvalidCredentials
    return tokens.issue(user.pk)
