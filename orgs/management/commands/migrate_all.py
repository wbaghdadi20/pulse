from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django_tenants.signals import schema_pre_migration

from orgs import services


class Command(BaseCommand):
    help = (
        "Migrate public, then the template new organizations are cloned from, then "
        "every organization, oldest first. Stops at the first schema that fails."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--no-input", action="store_false", dest="interactive")

    def handle(self, *args: Any, **options: Any) -> None:
        verbosity = options["verbosity"]
        interactive = options["interactive"]
        started: list[str] = []

        def note(schema_name: str, **kwargs: Any) -> None:
            started.append(schema_name)

        schema_pre_migration.connect(note, weak=False)
        try:
            call_command(
                "migrate_schemas",
                shared=True,
                interactive=interactive,
                verbosity=verbosity,
            )
            started.append(services.TEMPLATE_SCHEMA)  # its CREATE SCHEMA comes first
            services.migrate_template(verbosity=verbosity)
            call_command(
                "migrate_schemas",
                tenant=True,
                interactive=interactive,
                verbosity=verbosity,
            )
        except Exception as exc:
            failed = started[-1] if started else "public"
            raise CommandError(
                f"Migrating {failed} failed; the schemas after it were not migrated: {exc}"
            ) from exc
        finally:
            schema_pre_migration.disconnect(note)
