# 10. A check has a total deadline, not only a timeout per wait

- Status: accepted
- Date: 2026-11-11 (D7)

## Context

A monitor's `timeout_s` was passed to httpx as `timeout=`. In httpx that is four
separate limits: connecting, each wait for more bytes to read, each write, and
getting a connection from the pool. None of them limits the request as a whole.

So two kinds of slow target were treated differently:

- **Stuck and silent.** No bytes for `timeout_s`: httpx raises, the check is
  recorded with `error_type: timeout`.
- **Alive but slow.** A byte every few seconds, each gap shorter than
  `timeout_s`: httpx never raises. The check ran until Celery's 75 s limit
  killed the worker process, and no check row was written. Before that limit
  existed it ran for as long as the target kept dripping.

For a monitor the second case is the worse one to miss: the service is, for any
user, down.

## Options

1. **httpx's timeout alone** (what we had).
2. **A total deadline inside `probe`** (chosen): stream the response and look at
   the clock after every chunk.
3. **Celery's `soft_time_limit`**, catching the exception to record a timeout.
   Only exists inside a worker, so "check now" would behave differently, and it
   would put Celery exceptions in the service layer.
4. **An async client under `asyncio.timeout`.** An exact deadline over connect,
   headers and body. Needs an async path through Django views and Celery tasks,
   which are synchronous here.

## Decision

`checks._fetch` opens the request with `client.stream(...)`, still passing
`timeout=timeout_s` for the individual waits, and reads the body chunk by chunk.
After each chunk it compares the clock with `started + timeout_s`; past it, it
raises `httpx.ReadTimeout`, which `probe` records as `timeout` like any other.

The 75 s task limit from [0008](0008-one-check-per-interval.md) stays as the
backstop.

## Consequences

- A response that trickles is recorded as a timeout at about `timeout_s`.
- The deadline is checked between reads, not during one. A connect and a wait
  for the first bytes that each take almost `timeout_s` still add up to twice
  that. Headers that trickle are not seen at all, because httpx hands over
  nothing until they are complete. Those cases end at the 75 s kill, with no row.
- "Check now" gets the same deadline, since it calls the same `probe`. Its own
  backstop is gunicorn's 60 s worker timeout.
- `latency_ms` is still the time to the complete response, or to giving up.
- The body is collected in memory with no size cap. A very large response is
  read in full, up to the deadline.
- Alert delivery is unchanged: `PULSE_WEBHOOK_TIMEOUT_S` per wait, then retries.
