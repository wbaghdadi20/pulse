# 21. Tasks are acknowledged when taken; stopping waits for them instead

- Status: accepted
- Date: 2026-11-12 (D9)

## Context

Every deploy stops the worker. Docker sends SIGTERM, waits `stop_grace_period`,
then sends SIGKILL. A check can be in flight for up to 75 s. Celery by default
acknowledges a message when a worker takes it, before running it, so a task that
is killed mid-run is gone: the broker believes it was handled.

## Options

1. **Early acknowledgement, and a grace period longer than the longest task**
   (chosen). An orderly stop loses nothing. A kill loses what was running.
2. **`task_acks_late`**: acknowledge after the task returns. A killed worker's
   message stays unacknowledged and is delivered again. With Redis as the broker
   that happens when the visibility timeout passes: one hour by default.
3. **`task_acks_late` and a short visibility timeout.** Redelivery in minutes.
   The timeout has to stay above the longest task and the longest retry
   countdown, or a task that is still waiting or running is delivered a second
   time while the first is alive.

What option 2 would change, task by task:

| Task                 | Killed mid-run today                         | With `task_acks_late`                                   |
| -------------------- | -------------------------------------------- | ------------------------------------------------------- |
| `run_check`          | that check is lost; the next interval's runs | redelivered after an hour, but the message expires after `interval_s` and is dropped. Nothing gained |
| `send_alert`         | the alert stays `pending` for ever           | redelivered after an hour and sent                      |
| `enqueue_due_checks` | that tick is lost; the next one runs in 10 s | redelivered, finds nothing due                          |

Late acknowledgement makes a task at-least-once, so it is only safe for a task
that can run twice. `send_alert` can: it stops unless the row is still `pending`,
and the receiver drops a repeat by its `alert_id` (ADR 0006). `run_check` is safe
for a different reason: the lock and the expiry stop the second run.

## Decision

- Acknowledgement stays early.
- `worker` has `stop_grace_period: 80s`, above `CHECK_TIME_LIMIT_S` (75 s). `api`
  has 35 s, above gunicorn's `graceful_timeout` (30 s). `tests/test_project_files.py`
  fails if either pair stops agreeing.
- Every command is in exec form, so the process that handles SIGTERM is PID 1.

## Consequences

Measured with `flaky` answering after 20 s, stopping the worker 5 s into a check:

| Grace period | `docker compose stop worker` | Exit code     | The check                         | The lock                |
| ------------ | ---------------------------- | ------------- | --------------------------------- | ----------------------- |
| 80 s         | 16.8 s                       | 0             | finished and saved, 20,005 ms     | released                |
| 5 s          | 5.4 s                        | 137 (SIGKILL) | no row                            | left, 79 s still to run |

- With 80 s the worker logged `Warm shutdown`, took no new task, let the check
  finish, and exited as soon as it had: 16.8 s, not 80.
- With 5 s the check was killed. The next `run_check` for that monitor was
  skipped as "already being checked" until the lock expired.
- PID 1, timing `docker compose stop worker` on an idle worker with the 80 s grace
  period: 80.4 s and exit code 137 with a shell as PID 1 (`sh -c "celery ..."`,
  with or without a second command after it), 2.2 s and exit code 0 with the
  exec form. The shell never passes the signal on.
- A real crash (out of memory, a lost host) still loses what was running. For a
  check the cost is one sample and up to 90 s of lock. For an alert it is an
  alert nobody receives.
- For that case a periodic sweep that re-queues old `pending` alerts is the
  better repair than late acknowledgement: it also covers the broker being down
  when the alert was committed, which late acknowledgement does not, and it does
  not wait an hour. Not built (ADR 0006).
