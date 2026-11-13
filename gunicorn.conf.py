"""gunicorn's settings. It reads this file from the working directory when it starts."""

from config.env import env_int
from config.settings import LOGGING

bind = "0.0.0.0:8000"
workers = env_int("WEB_CONCURRENCY", default=2)
# A worker that has been silent this long is killed and replaced. "Check now" on a
# monitor with the longest timeout_s (30 s) has to fit inside it.
timeout = 60
accesslog = "-"
# The master process never loads Django, so it is handed the same logging setup.
logconfig_dict = LOGGING
