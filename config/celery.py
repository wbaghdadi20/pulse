"""The Celery app: `celery -A config worker|beat` starts from here.

Django settings prefixed CELERY_ become Celery settings (CELERY_BROKER_URL ->
broker_url); tasks are discovered in each installed app's tasks.py.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pulse")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
