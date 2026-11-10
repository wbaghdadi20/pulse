"""The Celery app. Worker and beat processes start from here: celery -A config ...

Settings come from Django's, prefixed CELERY_ (CELERY_BROKER_URL -> broker_url).
Tasks are found in each installed app's tasks.py.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pulse")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
