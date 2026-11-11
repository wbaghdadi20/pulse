"""A lock that every worker process sees, kept in Redis.

Why a token and a script: docs/adr/0008-one-check-per-interval.md
"""

import uuid

from . import clients

# A script runs as one step in Redis: no other command gets in between GET and DEL.
RELEASE_IF_OWNER = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def acquire(key: str, ttl_s: int) -> str | None:
    """Take the lock if nobody holds it. Returns the token that releases it, or None.

    SET NX EX looks and takes in one step, so two workers cannot both get it.
    The lock frees itself after ttl_s if it is never released.
    """
    token = uuid.uuid4().hex
    taken = clients.redis_client().set(key, token, nx=True, ex=ttl_s)
    return token if taken else None


def release(key: str, token: str) -> bool:
    """Free the lock if it is still ours. False: it expired, and may be someone else's now."""
    return bool(clients.redis_client().eval(RELEASE_IF_OWNER, 1, key, token))
