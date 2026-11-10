"""Settings for mypy's Django plugin, which runs django.setup() on the host.

Outside Compose the .env file is not loaded, and config.settings refuses to import
without its variables. Fake but well-formed values; nothing here connects to anything.
"""

import os

for _name in (
    "DJANGO_SECRET_KEY",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "CELERY_BROKER_URL",
):
    os.environ.setdefault(_name, "typecheck")

from .settings import *  # noqa: E402, F403
