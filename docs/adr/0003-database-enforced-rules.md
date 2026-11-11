# 3. Rules that can race are enforced by Postgres constraints

- Status: accepted
- Date: 2026-11-04 (D3), recorded 2026-11-10

## Context

Three rules must hold however many requests or workers run at once: monitor
names are unique, a monitor has at most one open incident per kind, and an
incident event produces at most one alert.

## Options

1. **Check, then insert, in Python.** `if not exists(): create()`. Two requests
   can both see "no" and both insert. DRF's `UniqueValidator` is this pattern.
2. **Lock first** (`select_for_update` on the monitor row, or an advisory lock).
   Correct, but every writer must remember to take the lock, and it serialises
   writers that would not have conflicted.
3. **Insert and let a constraint decide** (chosen).

## Decision

Each rule is a named constraint: `uq_monitor_name`,
`uq_one_open_incident_per_kind` (a partial unique index, `WHERE resolved_at IS
NULL`) and `uq_alert_incident_kind`. The code inserts inside
`transaction.atomic()` (a savepoint), catches `IntegrityError`, reads which
constraint fired (`core.db.violated_constraint`) and raises the matching domain
error. Any other integrity error is re-raised. The serializers' automatic
uniqueness validators are switched off so there is one source of truth.

Rules about a single row (ranges, enum values) cannot race. They are validated
at the API boundary by model validators and are not duplicated as `CHECK`
constraints.

## Consequences

- Correct under concurrency with no locks. Shown with two `psql` sessions and
  with 20 parallel requests: one 201, nineteen 409, no 500 (README).
- A duplicate name is a 409 from the database, not a 400 from a validator.
- The savepoint costs one extra round trip per insert.
- Reading the constraint name ties the code to psycopg's `diag`. Postgres only.
- Single-row rules are not protected against writers that skip the serializer
  (the shell, a data migration). `Check.objects.create(**result)` relies on
  `probe` producing valid values.
