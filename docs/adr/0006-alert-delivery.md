# 6. Alerts: row first, queue on commit, at-least-once delivery

- Status: accepted
- Date: 2026-11-10 (D6)

## Context

Opening or resolving an incident must notify a receiver once, even though a
task can run more than once and the receiver can be down.

## Options for sending

1. **POST inside the request or check.** A slow receiver would slow every
   check, and a failed POST has nowhere to be retried from.
2. **Queue a task carrying the payload.** No record of what was sent or failed.
3. **Insert an `alerts` row, then queue a task with its id** (chosen).

## Options for retrying

1. **`autoretry_for` with `retry_backoff`.** Least code. The attempt count
   lives only in the task message.
2. **`self.retry()` by hand** (chosen). The count is a column, incremented
   before each attempt, so it survives a crash and decides when to stop.

## Decision

- `queue_alert` inserts the row in the same transaction as the incident change.
  `uq_alert_incident_kind` makes a second insert for the same event a no-op.
- The task is queued with `transaction.on_commit`, so a worker never receives
  an id whose row is not visible yet.
- `send_alert` stops unless the row is `pending`, then counts the attempt, POSTs,
  and marks it `sent`. On failure it retries after `2^n` seconds plus up to the
  same again of jitter, `PULSE_ALERT_MAX_ATTEMPTS` times, then marks `failed`.
- The body is serialised once and those bytes are signed (HMAC-SHA256) and sent.
  The receiver verifies over the raw bytes with `hmac.compare_digest`.
- The payload carries `alert_id`, the row's id, the same on every attempt. It is
  in the body, not a header, because only the body is signed. The catcher stores
  each id once and answers a repeat with 200, so the sender stops retrying.

## Consequences

- Queued twice or run twice, an alert that was delivered is not sent again.
- At-least-once, not exactly-once: a timeout does not say whether the request or
  the answer was lost, so an attempt the receiver stored but answered too late is
  sent again. The receiver drops the repeat by its `alert_id`: delivered twice,
  stored once.
- The catcher keeps the ids in memory, like its inbox, so it forgets them on a
  restart and they are not shared between processes. A real receiver would use a
  unique constraint in the transaction that stores the alert.
- Two workers running `send_alert` for the same pending row at the same moment
  would both send, and the receiver would drop the second. Not observed; claiming
  the row with a conditional `UPDATE` before sending would avoid the request.
- If the broker is down at commit, or the worker dies mid-task, the row stays
  `pending`: the row and the message are two writes to two systems. The row is
  what makes this repairable: a periodic task that re-queues old pending rows
  closes the gap. Not built.
- Every delivery and failure is a row that can be queried.
