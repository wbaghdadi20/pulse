# 18. `/health` asks nothing of the dependencies, `/ready` asks them

- Status: accepted
- Date: 2026-11-12 (D9)

## Context

Two different questions get asked of a running service. "Is the process alive?"
decides whether to restart it. "Can it do its job right now?" decides whether to
send it traffic, and whether someone should be told. Pulse had one endpoint,
`/health`, which answers the first.

## Options for the endpoints

1. **One endpoint that also checks the database.** A database blip then makes a
   healthy process look dead, and whatever watches liveness restarts it. A
   restart does not bring a database back; it adds an outage of its own.
2. **Two endpoints** (chosen): `/health` touches nothing, `/ready` runs `SELECT 1`
   on Postgres and `PING` on Redis.

## Options for what `/ready` checks

- **Postgres**: every endpoint but `/health` needs it. Checked.
- **Redis, at `PULSE_REDIS_URL`**: the rate limit counts there on every request
  that needs a login, and the status cache and the check locks live there. Checked.
- **The broker**, as a third check: the same Redis server here, and the API only
  publishes to it. Not checked separately.
- **The alert receiver and the monitored targets**: someone else's outage must not
  make Pulse unready. Not checked.

## Options for a dependency that does not answer

1. **Wait for it.** A stopped container fails at once. A paused one, or a host
   that drops packets, holds the connect for the system's TCP timeout: minutes.
   Two such `/ready` calls occupy both gunicorn workers.
2. **A short timeout** (chosen): `PULSE_DEPENDENCY_TIMEOUT_S`, 2 s by default,
   bounded to 1 to 10 at startup (`pulse.E010`).

## Decision

- `core/readiness.py` holds the two checks and `answers()`, which runs both and
  never raises. `ReadyView` turns that into 200, or 503 with the failed ones named:
  `{"status": "unavailable", "checks": {"database": "failed", "redis": "ok"}}`.
- The reason for a failure is logged with its traceback and left out of the
  response: it names hosts and users, and `/ready` is public.
- Both endpoints are public and never rate limited: they are called by machines,
  and a 401 or 429 would read as "down".
- The timeout is the Postgres `connect_timeout` for every connection Pulse opens,
  not only this one, and the socket timeout of the Redis ping.
- The URLs have no trailing slash, like `/status`, `/login` and `/signup`. The
  ticket writes `/health/` and `/ready/`.

## Consequences

- Measured: 0.01 s with both up; 2 s for a paused dependency (the timeout); 4 s
  for a stopped container, because the name lookup is slow to fail and
  `getaddrinfo` has no timeout. Both stopped is about 8 s, so a monitor on
  `/ready` needs `timeout_s` of 10 or more.
- `/ready` costs a database connection and a Redis connection per call, from
  anyone. Cheap, and a proxy in front is where to limit it.
- **Pulse cannot alert about its own Postgres.** With a monitor on
  `http://api:8000/ready` and Postgres stopped for 70 s, `/ready` answered 503 the
  whole time and Pulse recorded nothing: beat's tick claims due monitors from
  Postgres and failed six times, so no check was queued, and a check, an incident
  and an alert are all rows in the database that was down. When Postgres came
  back, `/ready` was 200 again and no failure had been stored.
- It can alert about its API process. With `api` stopped for 57 s: five `dns`
  failures, an incident on the third, the `opened` alert a second later, and the
  `resolved` alert with `downtime_seconds: 36` after two good checks.
- So `/ready` is for something outside Pulse to call. A watcher that shares a
  database with what it watches goes blind exactly when that database fails. A
  second instance elsewhere, or a heartbeat to an outside service that alerts when
  it stops arriving, closes the gap. Not built.
- Allow-listing `api` for this has its own cost: ADR 0015.
