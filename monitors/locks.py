"""A lock that every worker process sees, kept in Redis."""

from . import clients


def acquire(key: str, ttl_s: int) -> bool:
    """Take the lock if nobody holds it. It frees itself after ttl_s if never released.

    SET NX EX looks and takes in one step, so two workers cannot both get it.
    """
    return bool(clients.redis_client().set(key, "locked", nx=True, ex=ttl_s))


def release(key: str) -> None:
    clients.redis_client().delete(key)
