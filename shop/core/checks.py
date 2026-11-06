"""The deep health check: which components /ht/ probes.

django-health-check 4.x takes check classes directly (no INSTALLED_APPS entries).
Each one runs with its own ~5 s budget; any failure turns the response into a 500.
With `Accept: application/json` the body has one key per check, named by repr():
{"Database(alias='default')": "OK", "Redis(db=0, host='shop-redis', port=6379)": "OK"}.
"""

from django.conf import settings
from health_check import Cache, Database
from health_check.contrib.redis import Redis
from redis.asyncio import Redis as RedisClient


def redis_client() -> RedisClient:
    return RedisClient.from_url(settings.REDIS_URL)


CHECKS = [
    Database,  # SELECT 1 on shop-db
    Cache,  # set/get through Django's cache, which is Redis-backed here
    (Redis, {"client_factory": redis_client}),  # PING shop-redis directly
]
