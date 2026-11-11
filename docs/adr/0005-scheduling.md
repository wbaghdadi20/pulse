# 5. Celery with Redis, one beat tick that queues what is due

- Status: accepted, due-detection superseded in D7
- Date: 2026-11-10 (D6)

## Context

Every monitor must be checked every `interval_s` seconds without anyone asking,
and monitors are created and deleted at runtime through the API.

## Options for running the work

1. **A loop or APScheduler inside the web process.** No new services. Every
   gunicorn worker would run its own scheduler, and a slow check would hold a
   request worker.
2. **Celery workers with Redis as the broker** (chosen). Named by the ticket,
   and Redis is needed in D7 anyway.
3. **Celery with RabbitMQ.** A purpose-built broker with durable queues. One
   more service to run for guarantees a check does not need: a lost check is
   replaced by the next one.

## Options for the schedule

1. **One beat entry per monitor.** Beat's schedule is static configuration;
   changing it at runtime needs `django-celery-beat` and its tables.
2. **One beat entry, a ten-second tick, and a query for what is due** (chosen).

## Decision

Beat queues `enqueue_due_checks` every 10 s. That task selects monitors whose
`last_checked_at` is null or older than `interval_s` and queues one
`run_check(monitor_id)` each. The schedule is data in `monitors`, so the API
changes it with no redeploy. Tasks take ids and load the row fresh.

## Consequences

- Workers scale horizontally; beat must stay at one instance.
- Intervals are quantised to the tick: a 10 s monitor is checked every 10 to 20 s.
- `run_check` never retries. A down target is a result, and a retry would turn
  one failed check into several.
- "Due" depends on `last_checked_at`, which moves only when a check finishes. A
  check that outlasts its interval, or a backlog, is queued again. This is the
  duplicate-check bug D7 fixes.
- Celery's default acknowledges a task when a worker takes it, not when it
  finishes. A worker killed mid-task loses that task.
