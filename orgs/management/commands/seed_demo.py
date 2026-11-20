from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from monitors import services as monitors
from monitors.models import Monitor
from orgs import services
from orgs.models import Membership, Organization

# Development only: these accounts and passwords are in the repository.
ORGANIZATIONS = [
    ("acme", "Acme", "owner@acme.test", "acme demo passphrase"),
    ("globex", "Globex", "owner@globex.test", "globex demo passphrase"),
]
# The D10 demo, in acme: shop with and without its dependencies (README, shop).
DEMO_ORG = "acme"
DEMO_MONITORS: list[dict[str, Any]] = [
    {
        "name": "shop",
        "url": "http://shop:8000/health",
        "timeout_s": 5,
        "interval_s": 10,
        "is_public": True,
    },
    {
        "name": "shop deep",
        "url": "http://shop:8000/ht/",
        "timeout_s": 5,
        "interval_s": 30,
        "is_public": True,
        "assertions": {
            "Database(alias='default')": "OK",
            "Redis(db=0, host='shop-redis', port=6379)": "OK",
        },
    },
]


class Command(BaseCommand):
    help = "Two demo organizations with an owner each, and the shop monitors in acme."

    def handle(self, *args: Any, **options: Any) -> None:
        # Known accounts with known passwords: never in a deployment.
        if not settings.DEBUG:
            raise CommandError("Development only: DJANGO_DEBUG is off.")
        for slug, name, email, password in ORGANIZATIONS:
            membership = self._organization(slug, name, email, password)
            self.stdout.write(f"/t/{slug}/  owner {email}  password {password!r}")
            if slug == DEMO_ORG:
                with membership.organization:
                    self._monitors(membership)

    @staticmethod
    @transaction.atomic
    def _organization(slug: str, name: str, email: str, password: str) -> Membership:
        user = User.objects.filter(email=email).first() or User.objects.create_user(
            email, password
        )
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            org = services.create_organization(name, slug)
        existing = Membership.objects.filter(user=user, organization=org).first()
        return existing or services.add_member(org, user, Membership.Role.OWNER)

    def _monitors(self, owner: Membership) -> None:
        for fields in DEMO_MONITORS:
            if not Monitor.objects.filter(name=fields["name"]).exists():
                monitors.create_monitor(owner, dict(fields))
                self.stdout.write(f"  monitor {fields['name']!r}")
