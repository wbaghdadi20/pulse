# 8. One check per interval: claim in Postgres, lock in Redis

- Status: accepted. Supersedes the due-detection part of [0005](0005-scheduling.md)
- Date: 2026-11-11 (D7)

## Context

With several workers, a monitor was checked more often than its interval, and by
two workers at once. Reproduced with three worker containers (16 processes each):

- flaky answering after 14 s, `interval_s = 10`, `timeout_s = 15`: checks ran in
  overlapping pairs, the second starting 4 s before the first had finished.
- workers stopped for 38 s and started again: six checks of the same monitor
  within one second.

One cause: a monitor was due while `last_checked_at + interval_s` had passed, and
`last_checked_at` only moves when a check finishes. Anything that delays the
finish (a slow target, busy or stopped workers) leaves the monitor due at the
next tick, so it is queued again.

There are two different things to prevent:

1. the same interval being **queued** more than once, and
2. two checks of one monitor **running** at the same time.

## Options

1. **A lock in Redis around `run_check`.** `SET lock:monitor:<id> <token> NX EX
   <ttl>`; a worker that does not get the key skips.
2. **Claim in Postgres when queueing.** A `next_check_at` column;
   `enqueue_due_checks` selects the due rows `FOR UPDATE SKIP LOCKED` and moves
   them to their next slot in the same transaction.
3. **Both** (chosen).
4. **Reject `timeout_s` greater than `interval_s`**, as Prometheus does for
   `scrape_timeout` and `scrape_interval`. With option 2 a check could then never
   still be running at its next slot. Not done now: D7 needs such a monitor to
   reproduce the bug. A sensible later addition.

|                             | Redis lock                                          | Postgres claim                                   |
| --------------------------- | --------------------------------------------------- | ------------------------------------------------ |
| Prevents                    | two checks of a monitor running at once             | a monitor being queued twice for one interval    |
| Acts                        | in the worker, when the check starts                | in the scheduler, before anything is queued      |
| A duplicate message         | is queued, delivered, then dropped                  | is never created                                 |
| Correct as long as          | the TTL outlasts every check: an assumption about time | the transaction commits: no assumption about time |
| When it goes wrong          | TTL too short: two holders, silently                | crash after commit, before queueing: that interval's check is lost |
| 14 s check, 10 s interval   | one check every 30 s (measured)                     | one every 10 s, overlapping (by construction)    |
| Lock store unavailable      | no checks                                           | unaffected                                       |
| Code                        | two functions and a Lua script, plus the TTL reasoning | one query, one column, one migration          |

If only one were allowed it would be the claim. It removes the cause instead of
filtering the effect, it does not depend on guessing a duration, and it makes
"what is due" an indexed lookup. The lock stays because the claim cannot stop a
check that outlasts its own interval from overlapping the next one.

## Decision

**Claim.** `services.claim_due_monitors` selects monitors with `next_check_at <=
now` using `select_for_update(skip_locked=True)` and updates `next_check_at` in
the same transaction. Two schedulers at the same moment get disjoint sets: the
second skips rows the first has locked instead of waiting for them. Slots stay
on a fixed grid (previous slot plus `interval_s`), so a scheduler that runs a few
milliseconds late does not push the monitor past the next tick. If a whole slot
was missed, the grid restarts from now; missed checks are not replayed. A queued
`run_check` expires after `interval_s`.

**Lock.** `run_check` takes `lock:monitor:<id>` before checking and skips if it
is held.

- `SET ... NX EX` tests and sets in one command. A separate "is it locked?" and
  "lock it" would let two workers both see "free".
- The value is a random token. Release is a Lua script that deletes the key only
  if it still holds that token, in one step.
- `run_check` has a hard Celery time limit of 75 s. The lock's TTL is 90 s.

### The lock-expiry bug this avoids

Reproduced with a 5 s TTL and 14 s checks. Worker A takes the lock and starts.
After 5 s the key expires although A is still checking. Worker B takes the lock
and starts: two holders. A finishes and deletes the key, which is now B's, so a
third worker could start too. The checks overlapped exactly as they had with no
lock at all.

Two independent faults, two fixes:

- *The lock expired under a running check.* A TTL is a bet on how long the work
  takes. `timeout_s` is at most 30 s, but a connect and a first read that each
  take almost that long add up to twice it (see [0010](0010-check-deadline.md)).
  Instead of trusting an estimate, the task is killed at 75 s and the lock lives
  90 s: a check cannot outlive its lock.
- *A released a lock it no longer owned.* The token makes ownership checkable,
  and the script makes "compare, then delete" one step. Done as two commands,
  the key could expire and be retaken between them.

## Consequences

- Measured with three workers for ten minutes on the reproduction monitor: the
  duplicate query returns no rows, no two checks overlap, 31 checks, each 20 s
  after the last. Stopping the workers for 38 s and restarting them produces one
  check, not six. A healthy 10 s monitor is checked every 10.0 s; with D6's
  scheduling it was every 20 s.
- Checks are at-most-once per interval. A crash between the claim's commit and
  queueing loses that interval's check; the next interval is unaffected. The
  previous design was at-least-once. For a monitor a missed sample is the
  cheaper error: duplicates distort uptime and can alert twice.
- A check slower than its interval is not checked every interval: the next
  slot's message finds the lock held and is skipped. Hence the 20 s above for a
  14 s check on a 10 s interval.
- A check killed at the 75 s limit writes no row. Its lock expires 15 s later.
- A worker that dies mid-check leaves its lock until the TTL runs out. Measured:
  `docker compose stop` killed a 14 s check at Docker's default 10 s grace, and
  that monitor went unchecked for 90 s. The worker's `stop_grace_period` is now
  80 s, so an orderly stop lets checks finish and release. A real crash still
  costs up to 90 s per monitor it was checking. A TTL is a trade: short and the
  lock lies, long and recovery is slow.
- "Check now" does not take the lock, so a manual check can overlap a scheduled
  one. The only shared decision, opening an incident, is already guarded by a
  unique index, and losing that race is handled.
- A Redis lock is not a guarantee in general: a process paused for longer than
  the TTL would still write. That is acceptable here because the damage is one
  extra check row; it would not be for something like a payment.
- `last_checked_at` no longer drives scheduling. It is kept as information.
- The lock is hand-written, about twenty lines, because the mechanism is the
  point of the exercise. `redis.lock.Lock` in redis-py is the same script plus
  blocking and extension, and is what to reach for otherwise.
