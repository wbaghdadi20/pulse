# 1. One shared HTTP client per process, with keep-alive off for checks

- Status: accepted
- Date: 2026-11-10

## Context

Pulse makes two kinds of outbound HTTP calls: checks against monitored targets
(`monitors/checks.py`) and alert webhooks to one receiver (`monitors/alerting.py`).

Until now both built a new `httpx.Client` for every call. A client owns the
connection pool and the TLS context, so each call rebuilt both. For checks the
client was also built inside the timed window, so `latency_ms` included it
(about 3 ms per check on a laptop, 36 ms for the first one in a process).

Django has no dependency-injection container like FastAPI's `Depends`. Shared
resources are module-level objects built on first use; `django.db.connections`
and `django.core.cache.caches` work this way.

## Options

1. **A new client per call** (what we had). Simple and isolated. Rebuilds the
   pool and TLS context every time and skews the latency measurement.
2. **One shared client with default pooling for everything.** The usual advice for
   an API that calls a backend. Wrong for checks: a connection kept alive from the
   previous check skips DNS, TCP and TLS, so latency reads lower than a visitor's
   and a target whose DNS record is gone stays "up" while the old connection lasts.
3. **Two shared clients, one per purpose** (chosen).

## Decision

`monitors/clients.py` exposes two accessors, each cached per process:

- `probe_client()`: `max_keepalive_connections=0`. Every check opens a new
  connection and so still exercises DNS, TCP and TLS. Prometheus' blackbox
  exporter does the same (`WithKeepAlivesDisabled()` in `prober/http.go`).
- `webhook_client()`: default pooling, timeout from `PULSE_WEBHOOK_TIMEOUT_S`
  (default 5 s, bounded 1 to 30 at startup). One receiver, called
  repeatedly; a burst of alerts during a large outage reuses one connection.

They are built on first use, not at import. gunicorn and Celery fork their
workers after importing the code, and a client built before the fork would be
shared with the parent process. Built lazily, each worker process has its own.

`probe(monitor, client=None)` keeps its `client` argument: that is the
injection point, and tests pass a client built on `httpx.MockTransport`.

## Consequences

- `latency_ms` measures the request only.
- Client configuration (limits, timeouts, later a User-Agent) lives in one file.
- The clients are never closed explicitly; their sockets close when the process
  exits. Acceptable for long-lived workers.
- Checks pay a TCP and TLS handshake every time. That is the cost being measured,
  not overhead to remove.
- `timeout_s` is applied by httpx to each phase (connect, read, write) rather than
  to the whole request, so a target that trickles bytes can outlast it. Unchanged
  by this decision; addressed in [0010](0010-check-deadline.md).
- `tests/monitors/test_clients.py` pins both behaviours by counting connections
  on a loopback server: 3 checks open 3 connections, 3 webhooks open 1.
