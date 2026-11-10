from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        from . import system_checks  # noqa: F401  (importing registers the check)
