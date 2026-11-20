# 14. A monitor is looked up together with its owner

- Status: accepted, amended 2026-11-20 (ticket 1, subtask 1.2)
- Date: 2026-11-12 (D8)

## Context

With accounts in place, any logged-in user could still read, change and delete
any monitor by putting its id in the URL (SECURITY.md, attack 2). Each monitor
needs an owner, and every path to a monitor has to respect it.

## Options

1. **Compare in each view**: load the monitor, then `if monitor.owner != user`.
   Seven places today. The one that is forgotten is the vulnerability.
2. **DRF object permissions** (`has_object_permission`). The framework's own
   mechanism. Its answer is 403, which confirms the id exists, and with plain
   `APIView` each view must remember to call `check_object_permissions`.
3. **Put the owner in the query** (chosen): `WHERE id = %s AND owner_id = %s`.
   A row that is not yours is never loaded.
4. **Postgres row-level security.** The database enforces it even for raw SQL.
   Every connection must first be told who the user is, and the workers, which
   act for all owners, need a way around it. Too much machinery for one table.

## Decision

- `Monitor.owner` is a required foreign key to the user, set from the token when
  the monitor is created. It is not a serializer field, so a body cannot set it.
- `services.get_monitor(pk, owner)` and `services.list_monitors(owner)` filter by
  owner. Views call `own_monitor(request, pk)`, which passes the caller. Checks,
  uptime and incidents hang off the monitor that call returns.
- Someone else's monitor raises `MonitorNotFound`, the same error and the same
  body as an id that was never used.
- Names are unique per owner (`uq_monitor_owner_name`). Unique across everyone,
  a 409 would tell one user which names another has taken.
- `Monitor.is_public` (default false) decides whether a monitor is on the status
  page. The page reads public monitors of every owner and nothing else. Creating,
  changing or deleting a public monitor drops the cached page on commit.
- Celery tasks load monitors by id without an owner: they act for the system.

## Consequences

- The owner is a required argument, so a new view that forgets it fails type
  checking before it fails a test.
- `tests/monitors/test_ownership.py` lists every route that takes a monitor id and
  compares the list with the URL configuration. A route added without a test fails.
- Deleting an account deletes its monitors, checks, incidents and alerts.
- There is no sharing: no teams, and no role that sees every monitor. Adding one
  means changing the filter in `services._monitors`, the one place it lives.
- A monitor made private can stay on a page that a request started building just
  before the change, for at most one cache lifetime (the race ADR 0009 describes).
- Monitors that existed before accounts have no owner to be given. Migration
  `0009` needs an empty `monitors` table.

## Amended 2026-11-20 (ticket 1, subtask 1.2)

A monitor belongs to an organization, and inside it is assigned to a membership
([ADR 0026](0026-schema-per-tenant.md)):

- The organization is the schema the monitor is in. A monitor of another
  organization is out of reach of every query, whatever the code filters.
- `Monitor.owner` became `Monitor.assignee`: a membership id with no database
  constraint (no foreign key crosses schemas), possibly empty. It is set from the
  caller's membership, never from the body.
  `orgs.services.require_active_member` refuses a membership that is missing,
  inactive or of another organization, and every service that sets an assignee
  calls it.
- `services.get_monitor(pk, assignee)` and `services.list_monitors(assignee)`
  filter by the caller's membership, until ticket 3's roles widen it. A monitor
  assigned to someone else answers 404, as before.
- Names are unique per organization (`uq_monitor_name`, ROLE-3), not per owner. A
  409 tells a member that a colleague uses that name, inside one organization
  only.
- The status page is per organization: its public monitors, whoever they are
  assigned to.
- Deleting an account no longer deletes its monitors, and is not supported: users
  are deactivated. Deleting one in `public` would leave its monitors assigned to a
  membership with no account; ticket 3 brings deletion, moving a leaver's monitors
  first.
