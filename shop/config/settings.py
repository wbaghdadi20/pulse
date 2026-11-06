"""shop: a small Django app that exists to be monitored.

It has the two dependencies a real service has (Postgres, Redis) and exposes:

- GET /          the catalogue, read from Postgres
- GET /health    shallow: "the process answers" (core/views.py)
- GET /ht/       deep (django-health-check, core/checks.py): database, cache and Redis, 500 if any fails

Configuration comes from SHOP_* environment variables set in compose.yaml.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SHOP_SECRET_KEY", "shop-demo-key-not-used-for-anything")
DEBUG = False
ALLOWED_HOSTS = ["*"]  # internal demo target, reached by service name over Docker

INSTALLED_APPS = [
    "health_check",  # templates for the HTML view of /ht/
    "core",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["SHOP_DB_NAME"],
        "USER": os.environ["SHOP_DB_USER"],
        "PASSWORD": os.environ["SHOP_DB_PASSWORD"],
        "HOST": os.environ["SHOP_DB_HOST"],
        "PORT": os.environ.get("SHOP_DB_PORT", "5432"),
    }
}

REDIS_URL = os.environ["SHOP_REDIS_URL"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
