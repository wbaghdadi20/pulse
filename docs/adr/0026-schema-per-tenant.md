# 26. One Postgres schema per organization, with django-tenants

- Status: proposed (spike SPK-1; to be finished in subtask 1.2)
- Date: 2026-11-19 (ticket 1, subtask 1.1)

## Context

Each customer becomes an organization, and ticket 1 decided to keep organizations
apart by giving each one its own Postgres schema. Before building on it, the
spike had one day to check that it works with what Pulse already has (the API,
Celery, beat's claim, the Redis locks and cache, the test suite) and to measure
what it costs. The proposed thresholds were: creating a tenant under 2 s, beat's
tick at 100 tenants under 1 s, the suite under twice today's time, and no test
that passes alone but fails in parallel. If one were missed, the decision would
be reopened with the mentor.

The spike is the branch `spike/tenancy-schemas`. It is not merged.

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

## What was built

- `django-tenants==4.0.6` and `tenant-schemas-celery==5.0.0`. Neither adds a
  dependency, and neither ships type hints: `orgs/tenancy.py` is a typed wrapper
  around what the library adds to the connection.
- Shared apps in `public`: auth, contenttypes, `core`, `accounts`, and a new
  `orgs` with `Organization` (`TenantMixin`), `Domain` (`DomainMixin`; in
  subfolder mode its `domain` holds the slug) and `Membership`. Tenant app:
  `monitors`. `core` has no models and stays shared. contenttypes is shared only:
  nothing uses content types, and keeping it out of the tenants means a clone
  needs no rows copied.
- Tenant URLs (`config/urls.py`) under `/t/<slug>/`; `/health`, `/ready`,
  `/signup`, `/login` in `config/urls_public.py`.
- The token's user and, inside a tenant, the caller's role there, in one query
  (a subquery on `memberships`). `orgs.permissions.IsMember` answers 404 to a
  valid token that is not a member, after `IsAuthenticated`'s 401.
- `config/celery.py` uses `tenant_schemas_celery.app.CeleryApp`. `enqueue_due_checks`
  and `roll_up_checks` loop over the tenants with `with org:` and run the existing
  claim in each; `run_check`'s message carries the schema in a header. The check
  lock is `lock:<schema>:monitor:<id>`. The cache uses
  `django_tenants.cache.make_key`, so every key starts with the schema.
- `orgs.middleware.TenantMiddleware`: the library's middleware with slug to
  tenant cached in the process, and no database access outside `/t/`.
- Tests: the public tenant, the template and two organizations (`acme`, `globex`)
  are created once per test database; fixtures `acme` (run inside it) and
  `globex`; `tests/orgs/test_tenancy.py` has 13 tests. With
  `PULSE_TEST_IN_TENANT=1` every database test runs in `acme` and the clients
  prefix tenant URLs, which is how the whole suite was measured.
- `manage.py migrate_template` and `manage.py spike_bench` (create, seed, tick).

Seen working on the spike stack (`-p pulse-spike`, api on 8110): two organizations
each create monitor 1 and each read their own back; Gina gets 404 on Acme, no
token 401, an unknown slug 404, the old `/monitors/` 404. Beat's tick queued both
tenants' checks ("queued 2 checks"), and the worker wrote one check row into
`org_acme.checks` and one into `org_globex.checks`.

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

`C exec -T api python manage.py spike_bench create --mode {migrate|clone} --count 9`,
alternated twice, with about 100 tenants already present:

| Mode | Medians of 9 |
| --- | --- |
| migrate | 154.5 ms, 154.9 ms |
| clone, with the constraint names restored | 156.5 ms, 161.9 ms |
| clone, before that fix (7 runs) | 127.2 ms |

At 13 migrations the two cost the same. Migrating grows with every migration
added; cloning does not.

### 2. Beat's tick

`C exec -T api python manage.py spike_bench seed --tenants N --monitors 3`, then
`spike_bench tick --runs 7` (each run makes every monitor due, ticks, then ticks
again with nothing due). The tick queues to the real Redis.

| Tenants | Every monitor due | Nothing due |
| --- | --- | --- |
| 2 (6 monitors) | 7.6 ms | 3.3 ms |
| 100 (300 monitors) | 348.2 ms | 118.3 ms |
| 100, `TENANT_LIMIT_SET_CALLS` off | 361.3 ms | 118.1 ms |
| 1 schema with 300 monitors (today's equivalent) | 141.6 ms | |

On the running worker with beat (100 tenants, checks running at the same time):
0.52, 0.40, 0.39, 0.38 s per tick, 300 queued each (`C logs worker`, the
`enqueue_due_checks ... succeeded in` lines).

Each tenant costs about 1.2 ms when idle (a transaction, `SET search_path`, the
claim) and about 2 ms when it has due monitors. At that rate the 1 s budget is
reached near 850 idle tenants, or about 280 tenants with three due monitors
each. Past that, the spec's next step applies: one claim task per tenant, run in
parallel.

### 3. Query count of `GET /t/<slug>/monitors/` (two monitors)

`tests/orgs/test_tenancy.py::test_listing_monitors_in_a_tenant_takes_this_many_queries`:

| Middleware | Queries | Which |
| --- | --- | --- |
| today, no tenants | 3 | user, COUNT, page |
| `TenantSubfolderMiddleware` (the library's) | 6 | `SET search_path` public, Domain + organization, `SET` tenant, user + role, COUNT, page |
| `orgs.middleware.TenantMiddleware` (slug cached) | 4 | `SET` tenant, user + role, COUNT, page |
| either, `TENANT_LIMIT_SET_CALLS` off | 8 and 6 | a `SET` before every query |

Caching slug to tenant removes the Domain lookup. The `SET search_path` remains:
one statement per request, which `assertNumQueries` counts.

`GET /health`: 2 queries with the library's middleware (it loads the public
tenant row), 0 with Pulse's.

In-process latency of `GET /t/<slug>/monitors/`, median of 5 x 300 requests
(`/tmp` script run with `manage.py shell`, not kept): 4.18 ms with the library's
middleware, 3.51 ms with the cached one. Not compared against today: NF-3's load
test is for subtask 1.3.

### 4. Tests

| What | Command | Result |
| --- | --- | --- |
| The tenancy tests, serial | `C exec -T api pytest -q -p no:cacheprovider tests/orgs` | 13 passed, 0.28 to 0.31 s |
| The same, parallel | `... -n auto tests/orgs` | 4.43 to 4.55 s, of which about 4 s is starting 16 workers (`tests/core/test_core.py` alone takes 4.35 s with `-n auto`) |
| Whole suite in a tenant, parallel | `C exec -T -e PULSE_TEST_IN_TENANT=1 -e PULSE_TENANT_CACHE=1 api pytest -q -n auto -p no:cacheprovider --deselect <the transactional test>` | 564 passed, 31 failed; 7.26, 7.05, 7.15, 7.15 s; 8.56 s with `--create-db` |
| The same, serial | without `-n auto` | 12.47 s, 12.39 s |

The suite takes the same time as today (7.1 s against 7.35 s, 12.4 s against
12.0 s serial). The same 31 failures came back in four parallel runs, and every
one of them is understood:

| Failures | Why | Fix |
| --- | --- | --- |
| 10 | auth tests that call tenant URLs with a plain `APIClient`, and the list of public routes, now in two URLconfs | mechanical |
| 9 | ownership tests: the other user is not a member, so 404 comes from the tenant before ownership | membership in the fixture; becomes TEN-8's test |
| 9 | query-count tests: one more statement, the `SET search_path` | new counts, or count without the `SET` |
| 2 | lock key format | mechanical |
| 1 | the request-id middleware must be first; so must the tenant's | put request id first (it touches no database) |

Converting the whole suite: the fixtures written here (an autouse tenant and a
client that adds `/t/acme`) carry 564 of 596 tests unchanged. The rest is about
half a day.

## Threshold verdicts

| Threshold | Measured | Verdict |
| --- | --- | --- |
| Creating a tenant < 2 s | 0.16 s, either way | met |
| Tick at 100 tenants < 1 s | 0.35 s in-process, 0.38 to 0.52 s on the worker | met |
| Suite < 2x today | 1.0x (7.1 s against 7.35 s) | met |
| No test passes alone but fails in parallel | the tenancy tests and the converted suite: same result every run. One existing test, the only `transaction=True` one, breaks it (below) | met once that test is fixed, which 1.3 must do |

## Decision

**Go**: schema per tenant with django-tenants and tenant-schemas-celery, path
routing under `/t/<slug>/`, tenants created by **cloning** a template, with these
changes to the library's defaults, each one found by the spike:

1. **After a clone, restore the template's constraint names.** The clone function
   (pg-clone-schema, shipped inside django-tenants) recreates every UNIQUE
   constraint under Postgres's default name: `uq_monitor_owner_name` becomes
   `monitors_owner_id_name_key`, and likewise `uq_alert_incident_kind` and
   `uq_check_hour_monitor_hour`. `core.db.violated_constraint` maps those names to
   409s, so a duplicate name became a 500 in every cloned tenant; and ROLE-3's
   migration, which removes `uq_monitor_owner_name`, would fail in each of them.
   `orgs.services.restore_constraint_names` renames them back (three renames,
   about 30 ms). Check constraints, foreign keys and indexes kept their names.
   1.2 needs a test that compares the template's catalog with a new tenant's.
2. **Clone, not migrate**, because signup must be one transaction (TEN-1), and
   migrating a new schema runs `AddIndexConcurrently`, which cannot be in one.
3. **Pulse's middleware** (`orgs.middleware.TenantMiddleware`): slug to tenant
   cached in the process, and nothing read from the database outside `/t/`.
4. **`TENANT_LIMIT_SET_CALLS = True`**, or every query is preceded by its own `SET`.
5. **`CELERY_TASK_TENANT_CACHE_SECONDS`**, or every task loads its tenant row first.

## Consequences

- A forgotten filter cannot leak between organizations: a query only sees its
  schema. A task or query with no tenant runs in `public`, where `monitors` does
  not exist, and fails (`test_a_task_without_a_tenant_fails_instead_of_reading_another`).
- Monitor, check and alert ids repeat across organizations, as TEN-4 expects.
  Sequences are per schema and not rolled back by a test's transaction, so a test
  that wants the same id in two tenants sets it.
- A request makes one statement more than today, the `SET search_path`, even with
  the slug cached. NF-1 ("no more queries than today") cannot be met as written.
  Either NF-1 counts queries without it, or 1.3 tries setting `search_path` when
  the connection opens (Pulse opens one per request, `CONN_MAX_AGE` 0, and the
  tenant is known before the first query).
- The clone function is about 4,600 lines of PL/pgSQL. django-tenants runs
  `CREATE OR REPLACE` on it (and `DROP TYPE public.cloneparms`) before every clone,
  and it lives in `public`: four functions and a type. Inside a transaction that
  takes a lock until commit, so signups serialize. Measured: three signups at once,
  each holding its transaction 2 s, finished after 2.9, 5.6 and 8.5 s. With a
  signup of about 160 ms that is six or seven a second at most; enough here, and creating the function once at
  migration time and calling `clone_schema()` directly would remove it.
- `migrate_schemas` migrates `public` and every tenant row, not the template:
  `make migrate` has to name it. With nothing to apply, the template alone takes
  0.82 s and 100 tenants 3.10 s, one after the other.
- Deleting a user no longer cascades from `public`: Django collects the user's
  monitors in the current schema only. From `public` the delete fails (no
  `monitors` table); from another tenant the foreign key refuses it; only inside
  the tenant that holds the rows does it work. Account deletion has to visit each
  organization the person belongs to. ADR 0014's "deleting an account deletes its
  monitors" changes.
- A test with `transaction=True` breaks the database: its teardown truncates
  `users`, which tenant tables reference, so the flush fails and leaves committed
  rows behind; with `--reuse-db` the next runs inherit them, and which tests then
  fail depends on how xdist distributed them (51 to 65 failures from one run to the
  next). Such a test needs its own cleanup of the tenant tables. A thread opens its
  own connection, in `public`, and must enter the tenant itself.
- An unknown slug is refused by the middleware with Django's HTML 404, before DRF
  and before the request-id middleware: no JSON body, no `X-Request-ID`, no log
  line. 1.3 moves the request-id middleware first and answers JSON.
- The library's middleware reads the public tenant row on every request outside
  `/t/`, `/health` included, which ADR 0018 says asks nothing of Postgres.
  Pulse's middleware does not.
- Rate-limit counters are Django cache keys, so they now carry the schema: a client
  gets its limit once per organization it calls. Locks are raw Redis and carry the
  schema because the key says so. 1.4 decides whether the general limit should
  stay per client across organizations.
- `transaction.on_commit` callbacks (the status page's invalidation) run in
  whatever schema the connection is in at commit. Inside one request or task that
  is the right one; code that switches tenant inside a transaction must commit first.
- No incompatibility with Django 6.1.2 or Celery 5.7.0: `manage.py check` is
  clean, `CeleryApp` builds its tasks on Celery's `DjangoTask`. tenant-schemas-celery
  calls `datetime.utcnow()`, deprecated in Python 3.14 (one warning per test run).
  django-tenants replaces `manage.py migrate` with `migrate_schemas`. Subfolder
  mode still needs the `Domain` table, holding a copy of the slug.

## Revised estimate for 1.2 to 1.5

| Subtask | Ticket's estimate | After the spike | Why |
| --- | --- | --- | --- |
| 1.2 Tenants exist | 2 days | 2 days | settings, `orgs`, clone and constraint fix are written; to add: atomic signup with the organization, the catalog test, `make migrate` and `make seed`, ROLE-3 |
| 1.3 Requests reach their tenant | 2 days | 2.5 days | middleware and the role in the token's query are written; to add: JSON 404 and request id first, `/me/organizations`, TEN-8's test, converting the suite (half a day, the transactional test included) |
| 1.4 Background work per tenant | 1 to 2 days | 1.5 days | the tick, roll-up, task header and locks are written; to add: log lines with the slug, alert payloads, the catcher's deduplication, rate-limit keys, account deletion across tenants |
| 1.5 The status API per tenant | 1 day | 1 day | the cache is already per tenant; CORS and the live page remain |
| Total | 7 to 8 days | about 7 days | |
