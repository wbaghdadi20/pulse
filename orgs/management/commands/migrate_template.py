from typing import Any

from django.core.management.base import BaseCommand

from orgs import services


class Command(BaseCommand):
    help = "Create or migrate the template schema new organizations are cloned from."

    def handle(self, *args: Any, **options: Any) -> None:
        services.ensure_public_tenant()
        services.migrate_template()
