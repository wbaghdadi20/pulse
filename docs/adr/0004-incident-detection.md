# 4. Incident detection is a pure function over the last checks

- Status: accepted
- Date: 2026-11-05 (D4), recorded 2026-11-10

## Context

An incident opens after N failed checks in a row and resolves after M passing
ones. One failure between successes must do nothing.

## Options

1. **Counters on the monitor row** (`consecutive_failures`,
   `consecutive_successes`), updated by each check. One row read per decision.
   The counters are derived data: two checks finishing at once can lose an
   update, and a check inserted out of order leaves them wrong for good.
2. **A stored state column** (`up` / `down`) plus counters. Same problem, and a
   second source of truth next to the incidents table.
3. **Recompute from the last `max(N, M)` check rows every time** (chosen).

## Decision

`incidents.decide(results, incident_open, fail_threshold, ok_threshold)` takes
booleans and returns `OPEN`, `RESOLVE` or `NOTHING`. No Django, no database.
`services.evaluate_incidents` loads the last `max(N, M)` checks ordered by
`checked_at` and whether an incident row is open, calls it, and applies the
answer. "Is this monitor down" has one definition: an incident row with
`resolved_at IS NULL`.

## Consequences

- The logic is tested exhaustively and cheaply: unit cases, Hypothesis
  properties, and mutation testing with no surviving mutants in the module.
- Self-healing: a crash between saving a check and evaluating it is repaired by
  the next check, because nothing is carried over between runs.
- Two queries per check instead of one row read. Both use an index and a LIMIT.
- N and M are global settings, bounded 1 to 10 at startup. Per-monitor
  thresholds would be two nullable columns and one line in the service.
- A hand-opened incident on a healthy monitor resolves after M passing checks,
  because the same rule applies to it.
