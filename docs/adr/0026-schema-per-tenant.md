# 26. One Postgres schema per organization, with django-tenants

- Status: accepted
- Date: 2026-11-19 (ticket 1, subtask 1.1, the spike), accepted 2026-11-20 (subtask 1.2)

## Context

Each customer becomes an organization, and ticket 1 decided to keep organizations
apart by giving each one its own Postgres schema. Before building on it, the
spike had one day to check that it works with what Pulse already has (the API,
Celery, beat's claim, the Redis locks and cache, the test suite) and to measure
what it costs. The proposed thresholds were: creating a tenant under 2 s, beat's
tick at 100 tenants under 1 s, the suite under twice today's time, and no test
that passes alone but fails in parallel. If one were missed, the decision would
be reopened with the mentor.

The spike is the branch `spike/tenancy-schemas`, never merged. Subtask 1.2 built
the decision properly; this ADR records both.

## Options

| Option | How tenants are kept apart | Gives | Costs |
| --- | --- | --- | --- |
| Shared tables with `organization_id` | a filter in every query | simplest; one migration run | one forgotten filter is a leak |
| **One schema per tenant** (decided in ticket 1, confirmed here) | Postgres's `search_path` | a forgotten filter cannot leak; a tenant is one schema to drop or export | migrations per tenant; every task carries its tenant |
| One database per tenant | separate databases | strongest | connections and backups per tenant |

For creating a tenant's schema:

| Option | Measured | Inside the signup transaction? |
| --- | --- | --- |
| Run its migrations | 155 ms (13 migrations) | **No**: migration 0011 uses `AddIndexConcurrently`, which Postgres refuses inside a transaction |
| **Clone a template schema** | 157 to 162 ms, including the constraint fix below | Yes |

For the reference from a tenant row (a monitor) to a person (decided with the
mentor in ticket 1):

| Option | Gives | Costs |
| --- | --- | --- |
| A foreign key to `public.users` (the spike) | the database refuses an unknown user | proves an account, not membership here; deletes in `public` blocked by every tenant; a tenant schema is not self-contained |
| A foreign key to `public.memberships` | proves a membership exists | still not that it is this organization's; the same cross-schema costs |
| **A membership id with no database constraint** | each schema stands alone; `public` rows can be removed without visiting tenants | nothing in the database stops a dangling or foreign id: the services do |

## Decision

**Schema per organization** with `django-tenants==4.0.6` and
`tenant-schemas-celery==5.0.0`, path routing under `/t/<slug>/`, tenants created
by **cloning** a template schema.

- Shared apps, in `public`: `django_tenants`, auth, contenttypes, staticfiles,
  `rest_framework`, `core`, `accounts`, and `orgs` with `Organization` (`TenantMixin`), `Domain`
  (`DomainMixin`; in subfolder mode its `domain` holds the slug) and `Membership`.
  Tenant app: `monitors`. contenttypes is shared only: nothing uses content types,
  and keeping it out of the tenants means a clone needs no rows copied. The public
  tenant row comes from migration `orgs.0002`.
- **The slug** is chosen at signup and never changes (`Organization.save` refuses
  a changed one). It is checked before any SQL (`orgs/slugs.py`): 3 to 40
  characters, lowercase letters, digits and single hyphens, a letter first, not on
  a short reserved list (`public`, the public tenant's own slug, and names that
  would pass for Pulse). No underscore, so the schema name, `org_` and the slug
  with `_` for `-`, is never the same for two slugs, and never `public`, the
  template or any other schema that is not an organization's. SQL that names a schema, a table or a constraint
  is built with `psycopg.sql.Identifier`, never by formatting strings.
- **Signup** (`accounts.services.signup`) creates the user, the organization
  (row, cloned schema, `Domain`) and the owner's membership in one transaction.
  Postgres's DDL is transactional, so a failure at any step, the clone included,
  leaves no account, no row and no schema.
- **A monitor is assigned to a membership**: `Monitor.assignee` is a
  `ForeignKey(Membership, null=True, on_delete=DO_NOTHING, db_constraint=False)`,
  so joins work and Postgres enforces nothing. `orgs.services.require_active_member`
  stands in for the constraint: every service that stores an assignee calls it,
  and it refuses a membership that is missing, inactive (its account is gone) or
  of another organization. `Membership.user` is an ordinary foreign key inside
  `public`, `SET_NULL`, so a membership outlives a purged account.
- **Monitor names are unique in the schema** (`uq_monitor_name`), so in the
  organization (ROLE-3): a monitor changes assignee, never organization.
- **Requests**: `config/urls_public.py` holds `/health`, `/ready`, `/signup`,
  `/login`; `config/urls.py` the tenant URLs, served under `/t/<slug>/`. The
  middleware remembers only slug to schema name. The token's user comes with the
  caller's active membership and its organization in one query, joined by that
  schema name (`orgs.services.active_membership`), and the organization row it
  returns is `request.tenant` (for code that needs the organization's row; only a
  test reads it so far). `orgs.permissions.IsMember` answers 404 to a valid
  token that is not a member of the request's organization, after
  `IsAuthenticated`'s 401.
- **Tasks**: `config/celery.py` uses `tenant_schemas_celery.app.CeleryApp`, so a
  message carries the schema it was queued from and runs there.
  `enqueue_due_checks` and `roll_up_checks` visit each organization in turn; one
  organization failing is logged with its slug and does not stop the others, and
  the task fails at the end, naming them. What is queued after commit (an alert's
  `send_alert`, dropping the cached status page) carries the schema it was
  registered in, not the one the connection is in when the transaction commits. The
  check lock is `lock:<schema>:monitor:<id>`; the cache's `KEY_FUNCTION` is
  `django_tenants.cache.make_key`, so every cache key starts with the schema.
  Rate-limit counters have a cache alias of their own without it: one count per
  client across organizations (ticket 1, TEN-4).
- **Migrations**: `manage.py migrate_all` (`make migrate`) migrates `public`, then
  the template, then every organization oldest first, and stops at the first
  schema that fails, naming it.

With these changes to the library's defaults, each found by the spike or by
reviewing it:

1. **After a clone, restore the template's constraint names.** The clone function
   (pg-clone-schema, shipped inside django-tenants) recreates every UNIQUE
   constraint under Postgres's default name: in the spike `uq_monitor_owner_name`
   became `monitors_owner_id_name_key` (today `uq_monitor_name` would become
   `monitors_name_key`), and likewise `uq_alert_incident_kind` and
   `uq_check_hour_monitor_hour`. `core.db.violated_constraint` maps those names to
   409s, so a duplicate name became a 500 in every cloned tenant.
   `orgs.services.restore_constraint_names` renames them back (about 30 ms).
   `tests/orgs/test_schemas.py` compares a new tenant's constraints, indexes and
   columns with the template's.
2. **Clone, not migrate**, because signup must be one transaction (TEN-1), and
   migrating a new schema runs `AddIndexConcurrently`, which cannot be in one.
3. **Clone only a template that matches the code exactly, and never one being
   migrated.** A clone's migrations are recorded from the code's files without
   running (`TENANT_CREATION_FAKES_MIGRATIONS`). From a template behind, a new
   organization would lack the latest migration while recording it as applied;
   from one ahead (old code still serving while a deploy migrates the template),
   it would hold a migration recorded as not applied, which the next
   `migrate_all` then fails to apply again, stopping at that organization.
   `create_organization` compares the template's `django_migrations` for the
   tenant apps with the migrations on disk and refuses unless they are equal:
   `TemplateNotReady`, a 503 with `Retry-After: 30`, its detail logged and not
   answered. A Postgres advisory lock, held exclusively by
   `migrate_template` and shared by every clone, keeps a clone from overlapping a
   template migration. The template is cloned with `NODATA`: it holds no rows, and
   none can spread to every new organization.
4. **Pulse's own middleware** (`orgs.middleware.TenantMiddleware`): slug to
   schema name cached in the process, nothing else (not the organization's id:
   a slug freed by a deletion in ticket 3 must not let its old members in), the
   URLconf built once per slug, and nothing read from the database outside
   `/t/`. A slug that cannot be a slug is a 404 without a query. It runs after
   the security and clickjacking middleware, so its 404 has their headers.
5. **`TENANT_LIMIT_SET_CALLS = True`**, or every query is preceded by its own `SET`.
6. **`CELERY_TASK_TENANT_CACHE_SECONDS = 60`**, or every task loads its tenant row first.
7. **`TENANT_REJECT_EXISTING_SCHEMA = True`**: a new organization never takes over
   a schema that is already there (one left behind, say), which would hand it
   whatever that schema holds.
8. **The schema is created by `create_organization`, not by saving the row**
   (`auto_create_schema = False`). The library's save cleans up a failed clone
   inside the transaction the failure has already aborted, so its own error
   replaces the clone's; called directly, the clone's error is the one raised, and
   the transaction's rollback is the cleanup.
9. **Organizations are created one at a time, and a signup waits for its turn a
   bounded time.** Every clone takes a second advisory lock, exclusively, after
   the shared template lock: concurrent signups queue on that one lock. Without
   it they collided inside django-tenants' DDL: before the first clone in a
   database, concurrent ones all ran `CREATE TYPE public.cloneparms` and all but
   one failed on `pg_type`'s unique index, a 500 (six at once: one created).
   The wait is `lock_timeout`, set for the transaction with
   `set_config('lock_timeout', ...)` from `PULSE_SIGNUP_LOCK_TIMEOUT_MS` (default
   2000, 100 to 30,000: a system check); past it, `OrganizationsBusy`, a 503 with
   `Retry-After: 5`. A waiting signup holds a gunicorn worker, so the wait is
   short. The loser of a race for one slug waits for the winner to commit, then
   finds the slug taken: a 409, not a 503, while the wait fits in the timeout.

## Measurements

All in the spike's containers on the same machine (16 CPUs). `C` stands for
`docker compose -p pulse-spike -f compose.yaml -f compose.override.yaml -f compose.spike.yaml`.

### Before the change (main at 215f96e)

| What | Command | Result |
| --- | --- | --- |
| Suite, parallel | `C exec -T api pytest -q -n auto -p no:cacheprovider` | 583 passed; 7.78, 6.98, 7.13, 7.57 s (first run 18.42 s, creating the databases) |
| Suite, serial | `C exec -T api pytest -q -p no:cacheprovider` | 11.97 s |
| Suite, new databases | `... -n auto --create-db` | 8.11 s |
| `GET /monitors/` | `tests/monitors/test_latest_check.py::test_listing_monitors_takes_three_queries_however_many_there_are` | 3 queries: the user, COUNT, the page (2 with `?after=`) |

### 1. Creating a tenant

`spike_bench create --mode {migrate|clone} --count 9` on the spike branch,
alternated twice, with about 100 tenants already present:

| Mode | Medians of 9 |
| --- | --- |
| migrate | 154.5 ms, 154.9 ms |
| clone, with the constraint names restored | 156.5 ms, 161.9 ms |
| clone, before that fix (7 runs) | 127.2 ms |

At 13 migrations the two cost the same. Migrating grows with every migration
added; cloning does not. In 1.2, a whole signup (password hash, user, clone,
renames, `Domain`, membership) took a median of 161.6 ms over 9 runs.

### 2. Beat's tick

`spike_bench seed --tenants N --monitors 3`, then `spike_bench tick --runs 7`
(each run makes every monitor due, ticks, then ticks again with nothing due). The
tick queues to the real Redis.

| Tenants | Every monitor due | Nothing due |
| --- | --- | --- |
| 2 (6 monitors) | 7.6 ms | 3.3 ms |
| 100 (300 monitors) | 348.2 ms | 118.3 ms |
| 100, `TENANT_LIMIT_SET_CALLS` off | 361.3 ms | 118.1 ms |
| 1 schema with 300 monitors (today's equivalent) | 141.6 ms | |

On the running worker with beat (100 tenants, checks running at the same time):
0.52, 0.40, 0.39, 0.38 s per tick, 300 queued each.

Each tenant costs about 1.2 ms when idle (a transaction, `SET search_path`, the
claim) and about 2 ms when it has due monitors. At that rate the 1 s budget is
reached near 850 idle tenants, or about 280 tenants with three due monitors
each. Past that, the next step is one claim task per tenant, run in parallel.

### 3. Query count of `GET /t/<slug>/monitors/` (two monitors)

| Middleware | Queries | Which |
| --- | --- | --- |
| today, no tenants | 3 | user, COUNT, page |
| `TenantSubfolderMiddleware` (the library's) | 6 | `SET search_path` public, Domain + organization, `SET` tenant, user + role, COUNT, page |
| `orgs.middleware.TenantMiddleware` (slug cached) | 4 | `SET` tenant, user + membership, COUNT, page |
| either, `TENANT_LIMIT_SET_CALLS` off | 8 and 6 | a `SET` before every query |

Caching slug to tenant removes the Domain lookup. The `SET search_path` remains:
one statement per request, which `assertNumQueries` counts. The first request for
a slug in a process adds two (`SET` public, the lookup).
`tests/orgs/test_tenancy.py` holds the 4, the first request's 6 and `/health`'s 0.

In-process latency of `GET /t/<slug>/monitors/`, median of 5 x 300 requests:
4.18 ms with the library's middleware, 3.51 ms with the cached one.

### 4. Tests

The spike ran the existing suite in a tenant behind a switch: 564 passed, 31
failed the same way in four runs, every failure understood (10 tenant URLs called
with a plain client, 9 ownership tests whose other user was not a member, 9 query
counts one statement short, 2 lock keys, 1 middleware order), in 7.1 s against
7.35 s in parallel and 12.4 s against 12.0 s serially.

In 1.2 the whole suite runs inside acme, on another stack on the same machine
(`exec -T api pytest -q -p no:cacheprovider`, as above):

| What | Result |
| --- | --- |
| Parallel, `-n auto` | 751 passed, 1 expected failure; 9.1 to 11.6 s over eight runs, the same result each time |
| Serial | 21.8 s |
| Parallel, `--create-db` | 10.9 s |

## Threshold verdicts

| Threshold | Measured | Verdict |
| --- | --- | --- |
| Creating a tenant < 2 s | 0.16 s, either way | met |
| Tick at 100 tenants < 1 s | 0.35 s in-process, 0.38 to 0.52 s on the worker | met |
| Suite < 2x today | parallel 1.2x to 1.6x, serial 1.8x, with about 170 tests more (two of them run pytest again in a subprocess) | met |
| No test passes alone but fails in parallel | the same result in every run, serial and parallel, once the transactional test was rewritten (below) | met |

## Consequences

- A forgotten filter cannot leak between organizations: a query only sees its
  schema. A task or query with no tenant runs in `public`, where `monitors` does
  not exist, and fails (`test_a_task_without_an_organization_fails_instead_of_reading_another`).
- Monitor, check and alert ids repeat across organizations, as TEN-4 expects.
  Sequences are per schema and not rolled back by a test's transaction, so a test
  that wants the same id in two tenants sets it.
- A request makes one statement more than before, the `SET search_path`. Subtask
  1.3 tries setting it when the connection opens (Pulse opens one per request,
  `CONN_MAX_AGE` 0); if that fails, one is NF-1's limit.
- What a database constraint did for owners, a service rule does for assignees:
  only `require_active_member` keeps a dangling or foreign membership id out of a
  tenant (`tests/conftest.py::assignee_problems` checks the rule in every
  organization). Deleting a membership or a user in `public` never touches a
  tenant, so a monitor could be left pointing at a membership whose account is
  gone: accounts cannot be deleted or deactivated yet. Ticket 3 brings both,
  deletion moving a leaver's monitors first, and locks the membership
  (`FOR SHARE`) while assigning.
- Signups take turns, by Pulse's lock and not by accident. Each clone also
  rewrites shared objects in `public` inside its transaction (django-tenants runs
  `CREATE OR REPLACE` on the clone function, about 4,600 lines of PL/pgSQL, and
  `DROP TYPE` on its parameter type, and the function creates and drops two types
  of its own on every call), so clones could never run side by side anyway.
  Installing the function once at migration time would not change that, because
  of the types the function makes itself. A signup takes about 160 ms, so six or
  seven a second at most: 25 at once gave 17 created, the slowest after 2.75 s,
  and 8 answered 503 after waiting 2 s. Not doing more until signups are that
  many.
- Anyone can sign up, and each signup creates a schema that stays: only the
  limit of 5 a minute per address (ADR 0016) bounds it. Each organization adds
  its tables to the catalog, a pass to `migrate_all` and about 1.2 ms to beat's
  tick, whose 1 s budget lasts to about 850 idle organizations. Limits per
  account and on organizations are ticket 5's (P2-5).
- A well-formed slug that does not exist costs one query (and a `SET`) every
  time, before any rate limit runs: unknown slugs are not remembered. Subtask 1.3
  revisits it with the 404's JSON body.
- The template must be migrated with every release, before any signup:
  `migrate_all` does it second, after `public`. `manage.py migrate` alone is the
  library's `migrate_schemas`, which skips the template; a signup then answers 503
  (`TemplateNotReady`, logged) instead of cloning a stale schema. With nothing to
  apply, the template alone took 0.82 s and 100 tenants 3.10 s.
- Deleting a user no longer deletes their monitors. ADR 0014 is amended.
- A test that commits (`transaction=True`) must not let pytest-django's flush run
  in `public`: it truncates organizations and memberships, the fixtures every
  other test needs. The tests that commit use the `committed` fixture, or the
  signup race's own: no test transaction, the tables emptied before and after,
  and a failure if anything was left in `public`. pytest-django sets up a test
  database only for tests marked to use one, so `tests/conftest.py` sets it up
  for these too, and they refuse to run on a database whose name does not start
  with `test_` (`tests/test_test_database.py` runs each alone against a
  development database that does not exist). One cut short can still leave rows
  behind in a kept test database, so before pytest-django migrates it,
  `tests/conftest.py` deletes every organization but acme and globex, any whose
  schema is gone, and every account. A thread opens its own connection, in
  `public`, and must enter the tenant itself.
- An unknown slug is refused by the middleware with Django's HTML 404, before DRF:
  no JSON body yet (subtask 1.3). The request-id middleware runs first, so the 404
  carries an `X-Request-ID` and is logged.
- `transaction.on_commit` callbacks run in whatever schema the connection is in at
  commit, which is not always the one they were registered in. The two Pulse has
  (queueing `send_alert`, dropping the cached status page) capture the schema when
  they are registered; any new one must too.
- Until subtask 1.4: the catcher drops a repeat by `alert_id` alone, so Globex's
  alert 1 is dropped as a repeat of Acme's alert 1; and log lines carry the
  request id, not the organization.
- No incompatibility with Django 6.1.2 or Celery 5.7.0: `CeleryApp` builds its
  tasks on Celery's `DjangoTask`. tenant-schemas-celery calls `datetime.utcnow()`,
  deprecated in Python 3.14 (one warning per test run). django-tenants replaces
  `manage.py migrate` with `migrate_schemas`. Subfolder mode still needs the
  `Domain` table, holding a copy of the slug.
- The load test (`LOADTEST.md`) still seeds and calls the layout from before;
  NF-3's run in subtask 1.3 adapts it.
