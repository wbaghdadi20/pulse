"""The Celery app: `celery -A config worker|beat` starts from here.

Django settings prefixed CELERY_ become Celery settings (CELERY_BROKER_URL ->
broker_url); tasks are discovered in each installed app's tasks.py.
"""

import logging.config
import os
from typing import Any

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pulse")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_logging(**kwargs: Any) -> None:
    """Celery normally replaces the root logger's handlers with its own format.
    With a receiver on this signal it leaves logging alone, so the worker and
    beat log through settings.LOGGING like the API does."""
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)
