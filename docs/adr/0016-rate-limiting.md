# 16. A fixed-window rate limit, counted in Redis, applied by DRF throttles

- Status: accepted
- Date: 2026-11-12 (D8)

## Context

`/login` answered about 1,300 password guesses a minute from one laptop
(SECURITY.md, attack 4), and nothing capped how often a client could call the
rest of the API, each call of "check now" being an outgoing request.

## Options for where

1. **A reverse proxy** (nginx `limit_req`). Refuses before a request reaches
   Python. The right place in production; this stack has no proxy.
2. **Django middleware.** Sees every request, including non-DRF views. A stricter
   limit for `/login` means matching paths by hand.
3. **DRF throttle classes** (chosen). Set per view, called by DRF on every
   request, and a refusal becomes 429 with `Retry-After` without more code.

## Options for the algorithm

1. **DRF's built-in throttles.** They keep a list of timestamps per client in the
   cache: read it, check it, write it back. Two requests arriving together both
   read the old list, and both pass.
2. **Fixed window** (chosen): one counter per client per clock minute, incremented
   atomically. Simple, one key per client. Allows up to twice the limit across a
   minute boundary.
3. **Sliding window counter.** Weighs the previous minute's count, which smooths
   the boundary. Two keys and some arithmetic.
4. **Token bucket.** Allows short bursts and a steady rate. Needs the read and
   the update to be one atomic step: a Lua script.

## Options for the store

1. **`clients.redis_client()` directly.** Every API test would then need a real
   Redis, shared between parallel test workers.
2. **Django's cache framework** (chosen). In production it is the same Redis:
   `add` is `SET NX EX` and `incr` is `INCR`. In tests it is the in-process cache
   the suite already uses, so the tests stay parallel-safe.

## Decision

- `core/ratelimit.py` holds the counting. A `Limiter` is any function
  `(key, limit) -> Decision(allowed, retry_after)`. `fixed_window` is the one that
  exists: key `rl:<scope>:<address>:<minute>`, created with a 60 s expiry on first
  use, then incremented.
- `core/throttling.py` holds two DRF throttles that are handed a limiter:
  `RateLimitThrottle` (`PULSE_RATE_LIMIT_PER_MINUTE`, default 60, DRF's default
  throttle) and `AuthRateThrottle` (`PULSE_AUTH_RATE_LIMIT_PER_MINUTE`, default 5,
  on `/login` and `/signup`, counted apart).
- The client is DRF's `get_ident`. `NUM_PROXIES` is `PULSE_NUM_PROXIES`, default 0:
  `X-Forwarded-For` is ignored and the connection's address is used. DRF's own
  default believes the header.
- `/health` and `/status` are not limited. A health probe must never be told 429.
- All three settings are bounded by startup checks.

**To add another algorithm:** write a function with the `Limiter` signature and
set it as `limiter` on a throttle (or a subclass). Neither the throttle nor the
views change. `tests/core/test_rate_limit.py` swaps one in to show it.

## Consequences

- Across a minute boundary a client gets up to twice the limit: five attempts at
  :59 and five at :00. Over time the rate is still the limit.
- The limit is per address. Users behind one NAT share a count; an attacker with
  many addresses, or an IPv6 /64, gets many counts.
- There is no lockout per account. It would stop guessing from many addresses,
  and it would let anyone who knows an email lock its owner out.
- DRF runs throttles after authentication. A request without a valid token to a
  protected endpoint is refused with 401 and not counted. That refusal costs a
  signature check and no query.
- With Redis down the API answers 500 rather than run unlimited. Redis is already
  required as the broker.
- The counters live in the cache database, so clearing the cache resets them.
