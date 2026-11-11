# 7. Targets and the receiver are separate services

- Status: accepted
- Date: 2026-11-05 (D4, flaky), 2026-11-06 (D5, shop), 2026-11-10 (D6, catcher)

## Context

Pulse needs things to monitor and somewhere to send alerts, all under our
control, and the demos need real failures on demand.

## Options

1. **Mock endpoints inside the Pulse app.** No new containers. Pulse would be
   monitoring itself: stopping the target stops the monitor.
2. **Public test sites.** Ruled out by the project rules for scheduled checks.
3. **Small separate services** (chosen).

## Decision

- `flaky` (FastAPI): one endpoint whose behaviour is switched between `ok`,
  `500` and `slow`. "Down" is stopping the container, so Pulse sees a real
  connection failure.
- `catcher` (FastAPI): verifies the signature, keeps alerts in memory, lists them.
- `shop` (Django, its own Postgres and Redis, django-health-check): its own
  Compose project, reached only over the shared `pulse-net` network. Its
  database and cache are not on that network.

`flaky` and `catcher` are FastAPI because they are a few routes with no
database. `shop` is Django because the point is a realistic service with real
dependencies and a deep health check.

## Consequences

- Failures are real: stopping `shop-redis` fails shop's deep check while its
  shallow one stays green, which Pulse can only tell apart with JSON assertions.
- Pulse cannot cheat. It has no route to `shop-db` or `shop-redis`.
- `pulse-net` is created by hand once, and Pulse must start before shop.
- Three more images to build. `flaky` and `catcher` share Pulse's Compose file
  for convenience, although architecturally they are outside the system.
- `catcher` keeps alerts in memory: restarting it empties the inbox.
