# Pulse

Pulse is an uptime monitor and status page. It checks HTTP services on a schedule,
records every result, opens and resolves incidents, sends signed alerts to a webhook,
and serves a public status page.

Built with Django, PostgreSQL, Celery, Redis and Docker Compose.

How it fits together: [ARCHITECTURE.md](ARCHITECTURE.md) (C4 diagrams, data model,
request and task flows). Why it is built this way: [docs/adr/](docs/adr/). How it
was attacked and fixed: [SECURITY.md](SECURITY.md).

## Prerequisites

- [Git](https://git-scm.com/)
- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2 (`docker compose version`)
- Python 3 (only to generate a secret key; the app itself runs in Docker)

## Quick start

1. Clone the repo:

   ```bash
   git clone https://github.com/wbaghdadi20/pulse.git
   cd pulse
   ```

2. Create your local environment file:

   ```bash
   cp .env.example .env
   ```

3. Generate two secrets and paste them into `.env` as `DJANGO_SECRET_KEY` and
   `PULSE_JWT_SECRET` (run it twice; they must differ):

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   Also change `POSTGRES_PASSWORD` to a password of your choice.

4. Create the network shared with the `shop` target, then build and start the stack:

   ```bash
   docker network create pulse-net
   docker compose up --build -d --wait
   ```

5. Create the database tables:

   ```bash
   docker compose exec api python manage.py migrate
   ```

6. Check it works:

   ```bash
   curl -i http://localhost:8100/ready
   ```

   Expected: `HTTP/1.1 200 OK` with
   `{"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`.

7. Create an account and log in. Everything except `/health`, `/ready` and
   `/status` needs the token:

   ```bash
   curl -X POST localhost:8100/signup -H 'Content-Type: application/json' \
     -d '{"email": "you@example.com", "password": "a long passphrase"}'
   TOKEN=$(curl -s -X POST localhost:8100/login -H 'Content-Type: application/json' \
     -d '{"email": "you@example.com", "password": "a long passphrase"}' \
     | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')
   curl -H "Authorization: Bearer $TOKEN" localhost:8100/monitors/
   ```

   The token is valid for an hour. The examples below assume `$TOKEN` is set.

8. Optional, for something worth monitoring: start the `shop` target (see _shop_ below):

   ```bash
   docker compose -f shop/compose.yaml up --build -d
   ```

## Services and ports

| Service    | Description           | URL / port on your machine                 |
| ---------- | --------------------- | ------------------------------------------ |
| `api`      | Django app            | http://localhost:8100                      |
| `postgres` | PostgreSQL 16         | `localhost:5433` (bound to 127.0.0.1 only) |
| `flaky`    | test target (FastAPI) | http://localhost:8200                      |
| `redis`    | Celery's queue (db 0), status page cache, check locks and rate limit counters (db 1) | not published |
| `worker`   | Celery worker: runs the scheduled checks and sends alerts | no port |
| `beat`     | Celery beat: the clock that queues due checks every 10 s  | no port |
| `catcher`  | alert inbox (FastAPI) | http://localhost:8400                      |

Host ports 8100 / 8200 / 8400 / 5433 are used instead of 8000 / 5432 so Pulse can run
alongside other local stacks. The `shop` target is a separate Compose project
(`shop/compose.yaml`) on http://localhost:8300.

## Everyday commands

| Command                                              | What it does                                     |
| ---------------------------------------------------- | ------------------------------------------------ |
| `docker compose up --build -d --wait`                | Build and start everything in the background     |
| `docker compose ps`                                  | Show running services and their health           |
| `docker compose logs -f api`                         | Follow the app's logs (Ctrl+C stops following)   |
| `docker compose logs -f worker`                      | Follow the checks and alerts as they run         |
| `docker compose exec api python manage.py <command>` | Run a Django management command                  |
| `docker compose down`                                | Stop everything (database data is kept)          |
| `docker compose down -v`                             | Stop everything **and delete the database data** |

## API

Built with Django REST framework. The first five endpoints are public. Every
other one needs `Authorization: Bearer <token>` and only ever sees the caller's
own monitors: without a valid token it answers 401, and for someone else's
monitor 404. Every response carries an `X-Request-ID` header (see _Logs_).

| Method + URL                                | Does                                              | Success | Errors        |
| ------------------------------------------- | ------------------------------------------------- | ------- | ------------- |
| `GET /health`                               | liveness: the process answers                     | 200     |               |
| `GET /ready`                                | readiness: Postgres and Redis answer too          | 200     | 503           |
| `GET /status`                               | public status page: state, 90-day uptime, p95, latest incidents of public monitors | 200 |         |
| `POST /signup`                              | create an account: `email`, `password`            | 201     | 400, 409, 429 |
| `POST /login`                               | exchange `email` and `password` for a token       | 200     | 400, 401, 429 |
| `POST /monitors/`                           | create a monitor                                  | 201     | 400, 409      |
| `GET /monitors/?limit&offset` or `?after`   | list monitors (paginated)                         | 200     | 400           |
| `GET /monitors/<id>/`                       | get one monitor                                   | 200     | 404           |
| `PATCH /monitors/<id>/`                     | change some fields (incl. `maintenance_until`)    | 200     | 400, 404, 409 |
| `DELETE /monitors/<id>/`                    | delete a monitor                                  | 204     | 404           |
| `POST /monitors/<id>/check/`                | call the monitor's URL once and record the result | 200     | 404           |
| `GET /monitors/<id>/uptime/?days=7\|30\|90` | uptime %, p95 latency and check count             | 200     | 400, 404      |
| `POST /monitors/<id>/incidents/`            | open an incident by hand                          | 201     | 404, 409      |

Example:

```bash
curl -X POST http://localhost:8100/monitors/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "httpbin", "url": "https://httpbin.org/get", "timeout_s": 5, "interval_s": 30}'
```

Conventions:

- **Login** returns `{"access_token": ..., "token_type": "Bearer", "expires_in": 3600}`.
  A wrong password and an unknown email get the same 401. The ticket's FastAPI
  dependency `get_current_user` is, in DRF, an authentication class
  (`accounts.authentication.JWTAuthentication`) plus `IsAuthenticated` as the
  default permission. [ADR 0013](docs/adr/0013-authentication.md).
- **A monitor belongs to whoever created it.** The owner comes from the token,
  never from the body, and names are unique per owner.
  [ADR 0014](docs/adr/0014-ownership.md).
- **`is_public`** (default `false`) puts a monitor on `/status`.
- **Monitor URLs must be on a public address.** A host that resolves to a private,
  loopback, link-local or otherwise internal address is refused with a 400 when
  the monitor is created or edited, and a check of one that turns internal later
  records `blocked`. Your own targets are the exception, by name:
  `PULSE_ALLOWED_PRIVATE_HOSTS`. Redirects are not followed: a 3xx is the result.
  [ADR 0015](docs/adr/0015-ssrf-guard.md).
- **Rate limits** are per client address and per clock minute: 60 requests on the
  API, 5 on `/login` and `/signup` together. Over the limit is a 429 with
  `Retry-After`. `/health` and `/status` are not limited.
  [ADR 0016](docs/adr/0016-rate-limiting.md).

- **Invalid input returns 400** with one message per field. That is DRF's
  convention (FastAPI uses 422).
- **URLs end with a slash** (`/monitors/`), the Django convention. A `POST` without
  the slash fails, because Django can't redirect a `POST` and keep its body.
- **Pagination** uses `?limit=` (default 20, max 100) and `?offset=`. The response is
  `{"count": <total>, "next": <url>, "previous": <url>, "results": [...]}`.
- **Keyset pagination** is the alternative: `?after=<last id seen>` (`0` to start)
  returns `{"next": <url>, "results": [...]}`, ordered by id. Follow `next` until
  it is `null`. It is the better choice when the list is long or changing:
  `OFFSET 499000` on a 500,000-row table reads 499,020 rows (68 ms) where
  `WHERE id > 499000` reads 20 (0.07 ms), there is no `COUNT(*)`, and a row
  deleted between two requests cannot make the next page skip one. Offset is the
  better choice when a client needs the total or wants to jump to a page.
  [ADR 0011](docs/adr/0011-keyset-pagination.md).
- **"Check now" always returns 200.** A target that is down is a result, not an
  error in Pulse. `error_type` is one of `timeout`, `connection_refused`, `dns`,
  `wrong_status`, `assertion_failed`, `blocked` (the host is on an internal
  address), or `error` (anything else, e.g. a TLS failure).
  `timeout` covers both a target that goes silent and one that keeps sending but
  has not finished within `timeout_s` in total
  ([ADR 0010](docs/adr/0010-check-deadline.md)).
- **JSON assertions** (optional field `assertions`): `{"<key>": "<expected value>"}`.
  When set, the check sends `Accept: application/json`, ignores the status code and
  passes only if every listed key has its expected value; otherwise `error_type` is
  `assertion_failed`. Built for deep health checks that answer 500 because of a
  component nobody cares about (see _shop_ below). Only flat string values, at
  most 20 keys.

### Incidents and flap protection

Every check result feeds a small state machine (`monitors/incidents.py`):

- No open incident and the last **N** checks all failed → open an incident.
- Open incident and the last **M** checks all passed → resolve it.
- Anything else (a single failed check between successes, not enough checks yet)
  → nothing. Networks blip; alerting on every blip trains people to ignore alerts.
- A monitor never has two open incidents of the same kind: the state machine won't
  ask for one, and the database forbids it anyway (see _Rules the database enforces_).
  Incidents carry a `kind`; `availability` is the only one so far, and the only one
  the state machine opens.

N and M are settings: `PULSE_FAIL_THRESHOLD` (default 3) and `PULSE_OK_THRESHOLD`
(default 2). The decision function is pure (no Django, no database) and takes the
recent results plus the current state; `services.evaluate_incidents()` loads those
from Postgres, calls it, and applies the answer.

#### Choosing N and M

The number that matters is not N itself but **time to detect = N × `interval_s`**:
with the defaults and a 30 s interval, Pulse needs 90 s to open an incident and
60 s to resolve one. 3-then-2 is not derived from our targets; it is the shape most
systems default to. HAProxy health checks default to `fall 3` / `rise 2`, and three
consecutive failures is also the default for Kubernetes probes (`failureThreshold`),
AWS Route 53 health checks (`FailureThreshold`, 3 × 30 s: the same 90 s) and
UptimeRobot. One failed check is noise (Azure reports that about 80% of single
failures disappear on retry), so two is the minimum debounce and three adds a margin.

Both settings must be between 1 and 10, the cap Route 53 and ALB use. A larger N
does not make detection safer: `decide()` needs N *consecutive* failures, so a
target that flaps would never open an incident at all. The bound is enforced by a
Django system check (`core/system_checks.py`, error id `pulse.E001`) that runs on
`manage.py check`, `migrate` and `runserver`, so a bad `.env` fails at startup
instead of silently muting a monitor. To detect slower, raise `interval_s` instead.

The values are a starting point, to be tuned from real incidents. `opened_at`,
`resolved_at` and the check history give the signals; each one points at one knob:

| Symptom in the data                                          | Diagnosis                            | Knob                                   |
| ------------------------------------------------------------ | ------------------------------------ | -------------------------------------- |
| Many incidents resolve almost as soon as they open           | N too low, or `timeout_s` too tight  | raise N, or lengthen `timeout_s` first |
| Long gap between the first failed check and the incident     | N too high for the interval          | lower N, or shorten `interval_s`       |
| Incidents reopen within minutes of resolving                 | M too low                            | raise M                                |
| Incident stays open while checks have been green for a while | M too high                           | lower M                                |
| Some monitors too noisy, others too quiet                    | global knobs don't fit every target  | per-monitor overrides (not built yet)  |

Nobody auto-tunes these counts: adaptive products (Datadog anomaly monitors,
Dynatrace baselining) learn *metric* baselines and need weeks of history. Sources:
[HAProxy health checks](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/health-checks/),
[Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/),
[Route 53 HealthCheckConfig](https://docs.aws.amazon.com/Route53/latest/APIReference/API_HealthCheckConfig.html),
[ALB health checks](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html),
[Google SRE Workbook, Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/).

### Which endpoints are idempotent

Idempotent means calling it twice leaves the system in the same state as calling it once.

| Endpoint                     | Idempotent? | Why                                                                                                                                                                        |
| ---------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET` (all)                  | yes         | reads only, changes nothing                                                                                                                                                |
| `PATCH /monitors/<id>/`      | yes         | it sets fields to values; setting `timeout_s` to 10 twice still leaves 10                                                                                                  |
| `DELETE /monitors/<id>/`     | yes         | after the first call the monitor is gone, and a second call changes nothing. The _response_ differs (204, then 404), but idempotency is about the effect, not the response |
| `POST /monitors/`            | no          | each call tries to create a new monitor (the second one gets 409 only because names are unique)                                                                            |
| `POST /monitors/<id>/check/` | no          | each call makes a new request to the target and records a new result                                                                                                       |

## Database

Postgres, through the Django ORM. Tables: `monitors`, `checks` (one row per check
result) and `incidents` (one row per outage; `resolved_at` is empty while it's open).

| Command (prefix with `docker compose exec api`) | What it does                                                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `python manage.py migrate`                      | apply all migrations (on an empty database: creates every table and index)                                    |
| `python manage.py makemigrations`               | generate a migration after changing `models.py`                                                               |
| `python manage.py sqlmigrate monitors 0002`     | show the SQL a migration will run                                                                             |
| `python manage.py seed_checks`                  | insert 500,000 fake checks for 10 `seed-N` monitors over the last 90 days (`--count`, `--monitors` to change) |

Change the schema only through migrations, never by hand in `psql`, so every
database ends up identical.

Open a SQL shell: `docker compose exec postgres psql -U <user> <db>`.

### Uptime query and its index

`GET /monitors/<id>/uptime/` runs a single aggregate query; no check rows are
loaded into Python. This is the SQL the ORM generates (`<since>` = now − days):

```sql
SELECT AVG(("checks"."ok")::integer) AS "up_ratio",
       percentile_cont(0.95) WITHIN GROUP (ORDER BY "checks"."latency_ms") AS "p95_latency_ms",
       COUNT("checks"."id") AS "checks"
FROM "checks"
WHERE ("checks"."checked_at" > <since> AND "checks"."monitor_id" = 4)
```

`EXPLAIN ANALYZE` on 500,000 seeded checks, 7 days, one monitor (3,899 matching rows):

**Before** (no index on `checks`):

```
Aggregate  (cost=9311.88..9311.89 rows=1 width=48) (actual time=10.646..12.674 rows=1 loops=1)
  ->  Gather  (cost=1000.00..9263.08 rows=3904 width=13) (actual time=0.316..12.178 rows=3899 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        ->  Parallel Seq Scan on checks  (cost=0.00..7872.68 rows=1627 width=13) (actual time=0.015..8.429 rows=1300 loops=3)
              Filter: ((monitor_id = 4) AND (checked_at > (now() - '7 days'::interval)))
              Rows Removed by Filter: 165367
Planning Time: 0.219 ms
Execution Time: 12.712 ms
```

**After** (index on `(monitor_id, checked_at)`, migration `0002`):

```
Aggregate  (cost=4087.75..4087.76 rows=1 width=48) (actual time=4.413..4.414 rows=1 loops=1)
  ->  Bitmap Heap Scan on checks  (cost=104.44..4038.95 rows=3904 width=13) (actual time=0.481..3.910 rows=3899 loops=1)
        Recheck Cond: ((monitor_id = 4) AND (checked_at > (now() - '7 days'::interval)))
        Heap Blocks: exact=2447
        ->  Bitmap Index Scan on checks_monitor_checked_idx  (cost=0.00..103.47 rows=3904 width=0) (actual time=0.321..0.321 rows=3899 loops=1)
              Index Cond: ((monitor_id = 4) AND (checked_at > (now() - '7 days'::interval)))
Planning Time: 0.262 ms
Execution Time: 4.482 ms
```

Without the index, Postgres reads all 500,000 rows and throws away 496,000 of them;
with it, Postgres jumps straight to the 3,899 rows for that monitor and time range,
so the query went from 12.7 ms to 4.5 ms.

Why this index: `monitor_id` comes first because the query filters it by equality,
then `checked_at` because it's a range. Because it starts with `monitor_id`, it also
covers lookups by monitor alone, so `checks.monitor` has no separate foreign-key
index (`db_index=False`). Most of the remaining time is fetching the matching rows
(`Heap Blocks: 2447`), because seeded rows are scattered randomly across the table.

### The latest check, without N+1

Every monitor in the API carries `latest_check` (its newest check, or `null`).
Asked for in the serializer, that is one extra query per monitor: 7 queries to
list 5 monitors. The list now gets it from the same statement, through a
correlated subquery that stops at the first entry of the `(monitor_id,
checked_at)` index:

| Listing 10 monitors with 50,000 checks each | Queries | Time   |
| ------------------------------------------- | ------- | ------ |
| one query per monitor                       | 11      | 10 ms  |
| one statement with a subquery               | 1       | 2 ms   |
| `prefetch_related` with a sliced queryset   | 2       | 435 ms |

The third row is why counting queries is not enough: it reads every check to
keep ten. [ADR 0012](docs/adr/0012-latest-check-without-n-plus-one.md).

### Rules the database enforces

| Rule                          | Constraint                                                                               | Violation → API |
| ----------------------------- | ---------------------------------------------------------------------------------------- | --------------- |
| Monitor names are unique per owner | `uq_monitor_owner_name` (unique on `(owner_id, name)`)                              | 409             |
| One account per email         | `uq_user_email` (unique), `ck_user_email_lowercase` (check)                              | 409             |
| One open incident per monitor and kind | `uq_one_open_incident_per_kind`: partial unique index on `(monitor_id, kind)` `WHERE resolved_at IS NULL` | 409             |

The code never asks "is the name free?" or "is an incident already open?" before
inserting. Two requests at the same moment could both get "yes" and both insert.
It inserts and lets Postgres decide. The `IntegrityError` is mapped to a 409 by
the constraint's name, inside `transaction.atomic()` (a savepoint) so the failure
doesn't break a surrounding transaction.

Two `psql` sessions, B starting while A's transaction is still open:

```
--- session A
BEGIN
INSERT INTO incidents (monitor_id, opened_at, cause) VALUES (8, now(), 'session A')
INSERT 0 1
SELECT pg_sleep(4)
COMMIT
A finished at 22:06:11
--- session B
B starts at 22:06:08 (A is still inside its transaction)
BEGIN
INSERT INTO incidents (monitor_id, opened_at, cause) VALUES (8, now(), 'session B')
ERROR:  duplicate key value violates unique constraint "uq_one_open_incident"
DETAIL:  Key (monitor_id)=(8) already exists.
COMMIT
ROLLBACK
B finished at 22:06:11
```

(Output from `psql -e`, trimmed: each command's echo is shown once, and the error
is moved to where it happened; psql prints errors on a separate stream. Postgres
answers B's `COMMIT` with `ROLLBACK`, because the transaction had already failed.)

B's insert waits until A commits, then fails. Through the API, 20 concurrent
`POST /monitors/5/incidents/` requests returned one 201 and nineteen 409s, and no 500s.

## Scheduled checks and alerts

Until D5 Pulse only checked when someone called "check now". Now it checks on its
own, with Celery:

```
beat ──every 10 s──▶ redis ──▶ worker: enqueue_due_checks()
                                  │  claim: SELECT id FROM monitors WHERE next_check_at <= now()
                                  │         FOR UPDATE SKIP LOCKED, then next_check_at = now() + interval_s
                                  └─▶ redis ──▶ worker: run_check(monitor_id)  (one message per claimed monitor)
                                                   │  lock:monitor:<id> in redis, skip if another worker holds it
                                                   └─▶ services.check_monitor(): the same code as "check now"
                                                         saves the check, stamps last_checked_at,
                                                         opens / resolves incidents
```

- **beat** is the clock. It never runs tasks; it puts one message on the queue
  every 10 seconds. Run exactly one: two beats queue everything twice.
- **worker** processes run the tasks. Arguments cross Redis as JSON, so tasks take
  ids and load the row fresh. A monitor deleted between tick and pop is logged and
  skipped, not an error.
- `monitors/tasks.py` holds the tasks; they are thin wrappers around
  `monitors/services.py`, so the API and the scheduler share one code path.
- Beat looks every 10 s, so a monitor is checked at the first tick after its slot.
  Slots stay on a fixed grid (previous slot + `interval_s`), so a 10 s monitor is
  checked every 10 s, not "10 s after the scheduler last got to it".

### Alerts

When an incident opens or resolves, Pulse POSTs a signed JSON webhook to the
catcher. The design is built around one fact: **a task can run twice** (a retry
after a timeout where the first attempt did get through, or the same event queued
twice). Three layers make that harmless:

1. **One row per event.** `alerts` has a unique constraint on `(incident_id, kind)`
   with kind `opened` or `resolved`. Recording the event twice is a no-op at the
   database, so an incident can never produce two "opened" alerts.
2. **The task checks before sending.** `send_alert(alert_id)` loads the row and
   stops if its status is no longer `pending`. Running it twice sends once.
3. **The receiver stores each alert once.** When an attempt times out, Pulse cannot
   tell a request that was lost from an answer that was lost, so it sends again:
   delivery is at-least-once. Every attempt carries the same `alert_id` inside the
   signed body, and the catcher answers an id it already stored with 200 without
   storing it again.

The row is inserted in the same transaction as the incident change, and the task is
queued with `transaction.on_commit`, so a worker can never pick up a message for a
row that isn't visible yet.

**Retries.** The receiver being unreachable, timing out or answering non-2xx is a
task failure: `send_alert` retries with exponential backoff and jitter (attempt *n*
waits between 2ⁿ and 2ⁿ⁺¹ seconds, so alerts that failed together don't retry
together), `PULSE_ALERT_MAX_ATTEMPTS` attempts in all (default 5), counting each attempt in `alerts.attempts` before the
request so a crash mid-request still counts. After the last failure the row is
marked `failed`; nothing stays `pending` forever. A *monitored target* being down is never a task
failure: `run_check` records the result and finishes normally, so it is never
retried and uptime numbers stay honest.

**Signing.** The payload is serialised once (`json.dumps` with sorted keys and
compact separators), signed with HMAC-SHA256 over the shared `PULSE_WEBHOOK_SECRET`,
and those exact bytes are sent with the hex digest in `X-Pulse-Signature`. The
catcher recomputes the digest over the raw body it received and compares with
`hmac.compare_digest`, which takes the same time whichever byte differs, so the
signature can't be guessed one character at a time. A wrong or missing signature
is a 401 and nothing is stored.

Payload, for example:

```json
{"alert_id": 52, "event": "incident.resolved", "monitor": "flaky", "monitor_id": 15,
 "incident_id": 37, "incident_kind": "availability", "cause": "3 failed checks in a row",
 "opened_at": "2026-11-10T03:18:42Z", "resolved_at": "2026-11-10T03:19:22Z", "downtime_seconds": 39}
```

### Maintenance windows

A planned deploy is not an outage. `PATCH /monitors/<id>/` with
`{"maintenance_until": "<ISO datetime in the future>"}` puts a monitor in
maintenance until then; `null` ends it now. While it lasts, checks still run and are
saved (uptime stays honest), but the state machine is skipped: no incident opens or
resolves and no alert is sent. The first check after the window judges normally, so
a service that is still down when the window ends gets its incident then. Opening an
incident by hand is not affected.

### catcher: the alert inbox

`catcher/` is a tiny FastAPI app standing in for Slack or PagerDuty. `POST /webhook`
verifies the signature and stores the alert; `GET /` (http://localhost:8400/) lists
everything received, newest first. An `alert_id` it has already stored is answered
200 and not stored again. Memory only: restarting it empties the inbox and forgets
the ids.

### Watch it work

```bash
docker compose logs -f worker                                   # one terminal
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "500"}'
# ~1 min later: three failed checks, an incident opens, "alert N sent: opened" in the log
curl -s localhost:8400/ | python -m json.tool | head -30       # the signed alert
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "ok"}'
# ~40 s later: two passing checks, the incident resolves, a second alert with downtime_seconds
docker compose stop catcher                                     # now break the receiver
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "500"}'
# the worker log shows five attempts with growing, jittered delays, then "failed after 5 attempts"
docker compose start catcher
```

Useful when something doesn't move:

```bash
docker compose exec worker celery -A config inspect ping        # is a worker listening on the queue?
docker compose exec worker celery -A config inspect registered  # which tasks it knows (unregistered task = module not imported)
docker compose exec redis redis-cli llen celery                 # messages waiting in the default queue
```

## One check per interval

With one worker Pulse looked correct. With three it checked the same monitor
several times per interval, sometimes from two workers at once. Why, the options
and the trade-offs: [ADR 0008](docs/adr/0008-one-check-per-interval.md).

### Reproducing the duplicates

```bash
docker compose up -d --scale worker=3
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "slow", "slow_seconds": 14}'
curl -X PATCH localhost:8100/monitors/<id>/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"interval_s": 10, "timeout_s": 15}'
```

A check now takes 14 s and is due every 10 s. Two queries show the damage. The
first finds checks that *finished* within 5 s of each other; the second finds
checks that were *in flight together*: a check's start is `checked_at` minus its
`latency_ms`.

```sql
-- finished close together
SELECT monitor_id, checked_at, gap FROM (
  SELECT monitor_id, checked_at,
         checked_at - lag(checked_at) OVER (PARTITION BY monitor_id ORDER BY checked_at) AS gap
  FROM checks WHERE checked_at > '<start of the run>'
) t
WHERE gap < interval '5 seconds';

-- in flight together
SELECT monitor_id, started_at, checked_at AS finished_at FROM (
  SELECT monitor_id, checked_at,
         checked_at - latency_ms * interval '1 ms' AS started_at,
         lag(checked_at) OVER (PARTITION BY monitor_id ORDER BY checked_at) AS previous_finished_at
  FROM checks WHERE checked_at > '<start of the run>'
) t
WHERE started_at < previous_finished_at;
```

Both need the time filter on a seeded database: `seed_checks` scatters its fake
checks at random, seconds apart.

Before the fix, on the code from D6:

| Scenario | What happened |
| -------- | ------------- |
| 14 s checks, 10 s interval | checks in overlapping pairs: one starts at :02, the previous one finishes at :06. The first query stays empty (the pair *finishes* 10 s apart); the second finds every pair |
| workers stopped for 38 s, then started | six checks of the monitor in one second: the first query returns five rows |

```
 monitor_id |          checked_at           |       gap
------------+-------------------------------+-----------------
         15 | 2026-11-11 07:25:15.277371+00 | 00:00:00.007184
         15 | 2026-11-11 07:25:15.28039+00  | 00:00:00.003019
         15 | 2026-11-11 07:25:15.291851+00 | 00:00:00.011461
         15 | 2026-11-11 07:25:15.411178+00 | 00:00:00.119327
         15 | 2026-11-11 07:25:16.297054+00 | 00:00:00.885876
```

The cause was the same both times. A monitor was "due" while `last_checked_at +
interval_s` had passed, and `last_checked_at` only moved when a check *finished*.
A slow check, or workers that were away, left the monitor due at the next tick.

### The fix

Two mechanisms, each for one problem:

- **Claim in Postgres** so an interval is queued once. `enqueue_due_checks`
  selects due monitors `FOR UPDATE SKIP LOCKED` and moves `next_check_at` to the
  next slot in the same transaction.
- **Lock in Redis** so a monitor is checked by one worker at a time. `run_check`
  starts with `SET lock:monitor:<id> <token> NX EX 90` and skips if the key exists.

|                           | Redis lock                                               | Postgres claim                                    |
| ------------------------- | -------------------------------------------------------- | ------------------------------------------------- |
| Simpler?                  | two functions and a Lua script, plus a TTL to get right  | one query and one column: the simpler of the two  |
| Survives Redis being down? | no: without the lock store no check runs                 | yes, it only needs Postgres. In this stack Redis is also the queue, so nothing is queued either way |
| 14 s check, 10 s interval | one check every 30 s                                     | one every 10 s, overlapping                       |
| If only one               |                                                          | this one: it removes the cause and assumes nothing about how long a check takes |

Both are kept because they stop different things. The claim cannot prevent a
check that outlasts its interval from overlapping the next; the lock cannot
prevent the duplicate message from being queued in the first place. Together, the
slow monitor above is checked every 20 s, one check at a time.

```bash
docker compose exec redis redis-cli -n 1 keys 'lock:*'       # held locks
docker compose exec redis redis-cli -n 1 ttl lock:monitor:<id>
```

### When the lock lies

A first version of the lock had a 30 s TTL and released with a plain `DEL`. With
the TTL set to 5 s and checks taking 14 s:

1. Worker A takes the lock and starts checking.
2. After 5 s the key expires. A is still checking.
3. Worker B takes the lock and starts too. Two workers now "hold" it.
4. A finishes and deletes the key. It is B's key. A third worker could now start.

The checks overlapped exactly as they had without a lock. Two things were wrong,
and each has its own fix:

- **The lock expired under a running check.** `timeout_s` is at most 30 s, but a
  connect and a first read that each take almost that long add up to twice it.
  Rather than trust an estimate, Celery kills a `run_check` at 75 s and the lock
  lives 90 s. A check can no longer outlive its lock.
- **A deleted a lock that was no longer its own.** The key's value is now a random
  token, and release is a Lua script that deletes the key only if it still holds
  that token. A script runs as one step in Redis; "read, compare, delete" as
  separate commands would leave a gap for the key to expire and be retaken.

`tests/monitors/test_locks.py` replays the four steps against a real Redis.

The long TTL has a price: a worker that dies mid-check leaves its lock behind,
and that monitor is not checked until the lock expires. An orderly stop avoids
it, because the worker's `stop_grace_period` (80 s) lets running checks finish.

### After the fix

Three workers, the same slow monitor, ten minutes: both queries return no rows.
31 checks, each 20 s after the last; 35 queued checks were skipped because the
monitor was still being checked. Stopping the workers for 38 s and starting them
again: no rows either, and one check where there had been six.

## Status page

`GET /status` is public: no login. For each monitor with `is_public` set, whoever
owns it, it returns `state` (`down` while an incident is open, otherwise `up`),
`uptime_percent` and `p95_latency_ms` over the last 90 days, and its three latest
incidents. It never returns a monitor's URL or owner, and a private monitor is
not on it at all.

```bash
curl -s localhost:8100/status | python -m json.tool
```

The page takes four queries whatever the number of monitors, and one of them
aggregates 90 days of checks. So it is cached in Redis under `status:page` for
`PULSE_STATUS_CACHE_SECONDS` (default 15): a request returns the cached page if
there is one, otherwise builds it, stores it and returns it. When an incident
opens or resolves the key is deleted, so the next visitor sees the change at
once instead of up to 15 s later. Options and trade-offs:
[ADR 0009](docs/adr/0009-status-page-cache.md).

```bash
docker compose exec redis redis-cli -n 1 ttl :1:status:page   # Django prefixes keys with ":1:"
```

### Benchmark

10 monitors, 500,000 seeded checks, 60 incidents. gunicorn with 2 workers,
`DJANGO_DEBUG=false`. 2000 requests from 50 concurrent keep-alive clients, the
shape of `hey -n 2000 -c 50 http://localhost:8100/status`, sent by a small httpx
script. "Without cache" is `PULSE_STATUS_CACHE_SECONDS=0`; "with cache" starts
from an empty cache, so it includes the first misses.

|                         | p50      | p95       | p99       | throughput |
| ----------------------- | -------- | --------- | --------- | ---------- |
| without cache           | 9,192 ms | 10,505 ms | 11,828 ms | 6 req/s    |
| with cache (15 s)       | 50 ms    | 92 ms     | 579 ms    | 718 req/s  |

One client at a time, no queueing: 425 ms (p50) and 504 ms (p95) without the
cache, 2.7 ms and 4.2 ms with it.

Without the cache every request runs the 0.4 s aggregate and two workers serve
50 clients, so most of the nine seconds is waiting in line. With it, almost every
request is one Redis read. The cached p99 is the stampede: the first 50 requests
all missed and all built the page.

## Logs

Every process (`api`, `worker`, `beat`) writes one JSON object per line to stdout,
where Docker collects it ([ADR 0017](docs/adr/0017-json-logs-and-request-ids.md)).

```bash
docker compose logs -f api worker                              # as written
docker compose logs --no-log-prefix api | jq -R 'fromjson?'    # readable
docker compose logs --no-log-prefix worker | jq -R -c 'fromjson? | select(.level == "ERROR")'
```

`jq -R 'fromjson?'` skips the few lines that are not JSON, such as runserver's
start-up banner in development.

| Key                                         | On                                                        |
| ------------------------------------------- | --------------------------------------------------------- |
| `time`, `level`, `logger`, `message`        | every line                                                |
| `request_id`                                | every line written while a request is served, and by a task that request queued |
| `task_id`, `task`                           | every line written inside a Celery task                   |
| `method`, `path`, `status`, `duration_ms`   | `request finished`, one per request                       |
| `exc_info`                                  | a line that logs an exception: the traceback, as one string |

`PULSE_LOG_LEVEL` sets the level for all three.

### Following one request

Each request gets an id: the caller's `X-Request-ID` if it sent one of 1 to 64
characters from `[A-Za-z0-9._-]`, otherwise a new uuid4. It comes back in the
response's `X-Request-ID` header and is on every line the request causes,
including in the worker. Here the third failed "check now" opens an incident, and
the alert it queues is delivered by the worker under the same id:

```bash
curl -X POST localhost:8100/monitors/8/check/ -H "Authorization: Bearer $TOKEN" \
  -H 'X-Request-ID: demo-check-3'
docker compose logs --no-log-prefix api worker | jq -R -c 'fromjson? | select(.request_id == "demo-check-3")'
```

```json
{"level": "INFO", "logger": "core.middleware", "message": "request finished", "method": "POST", "path": "/monitors/8/check/", "status": 200, "duration_ms": 86, "request_id": "demo-check-3", "time": "2026-11-13T08:02:47.486868+00:00"}
{"level": "INFO", "logger": "monitors.tasks", "message": "alert 3 sent: opened", "request_id": "demo-check-3", "task_id": "7dbfae3c-c642-4c6e-a636-5b15427f683f", "task": "monitors.tasks.send_alert", "time": "2026-11-13T08:02:47.497594+00:00"}
```

A scheduled check was queued by beat, not by a request, so its lines have a
`task_id` and no `request_id`.

### What is never logged

Query strings, request headers and request bodies: that is where tokens,
passwords and keys travel. The request line has the path only. Monitors are
logged by id, and httpx's own line per outgoing request, which has the full URL,
is turned off. `tests/core/test_request_id.py` logs in and makes a request with a
token and a query string, then checks none of the three is in any line.

## Health and readiness

|                    | `GET /health`                         | `GET /ready`                                        |
| ------------------ | ------------------------------------- | --------------------------------------------------- |
| Asks               | nothing                               | `SELECT 1` on Postgres, `PING` on Redis             |
| 200 means          | the process answers                   | and it can do its job                               |
| Otherwise          | no answer at all                      | 503, with the failed dependency named               |
| For                | whatever restarts a dead process      | whatever decides to send traffic, or to tell someone |

```bash
$ curl -s localhost:8100/ready
{"status":"ok","checks":{"database":"ok","redis":"ok"}}
$ docker compose stop postgres
$ curl -s -w ' [%{http_code}]' localhost:8100/ready
{"status":"unavailable","checks":{"database":"failed","redis":"ok"}} [503]
$ curl -s -o /dev/null -w '%{http_code}' localhost:8100/health
200
```

`/health` must not check the database. If it did, a database blip would make a
healthy process look dead, and whatever restarts dead processes would restart it,
which fixes nothing and adds an outage ([ADR 0018](docs/adr/0018-health-and-readiness.md)).

A dependency that hangs must not hang `/ready`. Each check gives up after
`PULSE_DEPENDENCY_TIMEOUT_S` (2 s). Measured: 0.01 s with both up, 2 s for a
paused container, 4 s for a stopped one (the name lookup is slow to fail), 8 s
with both stopped. The reason for a failure is in the log, with its traceback and
the request id, and not in the response: `/ready` is public.

### Pulse watches itself

`api` is in `PULSE_ALLOWED_PRIVATE_HOSTS` and in `DJANGO_ALLOWED_HOSTS`
(`.env.example`), so a monitor can point at Pulse's own readiness:

```bash
curl -X POST localhost:8100/monitors/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "pulse-ready", "url": "http://api:8000/ready", "interval_s": 10, "timeout_s": 10}'
```

**Stopping the API** (`docker compose stop api`, 57 s) is noticed:

| Time     | What Pulse recorded                                                |
| -------- | ------------------------------------------------------------------ |
| 08:00:40 | `api` stopped                                                      |
| 08:00:54 | first failed check, `dns`; four more follow, 10 s apart            |
| 08:01:14 | third failure: incident opened                                     |
| 08:01:15 | `incident.opened` in the catcher                                   |
| 08:01:37 | `api` started                                                      |
| 08:01:50 | second good check: incident resolved, `incident.resolved` with `downtime_seconds: 36` |

**Stopping Postgres** (`docker compose stop postgres`, 70 s) is not:

| What                                     | Result                                                        |
| ---------------------------------------- | ------------------------------------------------------------- |
| `/ready`, asked from outside             | 503, `"database": "failed"`, the whole time                   |
| `/health`                                | 200                                                           |
| beat's tick in the worker                | failed six times: `OperationalError: failed to resolve host 'postgres'` |
| Checks recorded for `pulse-ready`        | none between 07:59:00 and 08:00:10                            |
| Failed checks, incidents, alerts         | 0, 0, 0                                                       |
| After `docker compose start postgres`    | `/ready` 200, checks resume, nothing says there was an outage |

The scheduler claims due monitors from Postgres, and a check, an incident and an
alert are all rows in Postgres. With it down there is nothing to queue and nowhere
to write. Pulse can report that its API is gone; it cannot report that its own
database is, because that is the one thing it needs in order to report anything.
`/ready` is the right signal, but something outside Pulse has to be the one
asking: a second instance elsewhere, or a heartbeat to an outside service that
alerts when the heartbeat stops.

## Stopping without losing work

Every deploy stops the worker and starts a new one. `docker compose stop` sends
SIGTERM to PID 1 of each container, waits `stop_grace_period`, then sends SIGKILL.
On SIGTERM Celery does a _warm shutdown_: it takes no new task, finishes the ones
that are running, and exits. That saves the work only if the grace period is
longer than the longest task, and if the signal reaches Celery at all.

### The grace period

`flaky` answering after 20 s, one check queued by hand, the worker stopped 5 s
into it:

```bash
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' \
  -d '{"mode": "slow", "slow_seconds": 20}'
docker compose stop beat        # nothing else gets queued while measuring
docker compose exec api python manage.py shell -c \
  "from monitors.tasks import run_check; run_check.delay(8)"
sleep 5; time docker compose stop worker
```

| `stop_grace_period` | `stop` took | Exit code     | The check                      | Its lock                |
| ------------------- | ----------- | ------------- | ------------------------------ | ----------------------- |
| 80 s (the setting)  | 16.8 s      | 0             | finished and saved, 20,005 ms  | released                |
| 5 s                 | 5.4 s       | 137 (SIGKILL) | no row                         | left, 79 s still to run |

With 80 s, the worker's log (`worker: Warm shutdown` is printed by Celery, not
logged, so it is the one plain line):

```
08:03:39 Task monitors.tasks.run_check[229b3530-...] received
worker: Warm shutdown (MainProcess)
08:03:59 monitor 8 ok=True error=None
08:03:59 Task monitors.tasks.run_check[229b3530-...] succeeded in 20.019698635005625s: None
```

The stop returned when the check was done, not after 80 s. With 5 s the check was
killed with nothing saved, and its lock outlived it: the next check of that
monitor logged `monitor 8 is already being checked, skipping`, and the monitor
went unchecked until the lock expired.

So the grace period has to be longer than the longest task. Celery kills a
`run_check` at 75 s (`CHECK_TIME_LIMIT_S`: twice the largest `timeout_s`, plus a
margin), which makes 80 s enough whatever a target does. `api` gets 35 s, because
gunicorn waits up to 30 s for requests in flight. `tests/test_project_files.py`
fails if either number drops below what it has to exceed.

### PID 1

The signal goes to PID 1 only. If that is a shell, Celery never hears it: the
shell does not pass SIGTERM on, and as PID 1 it ignores a signal it has no handler
for. Docker then waits the whole grace period and kills everything. Timing
`docker compose stop worker` on an idle worker:

| `command:` for the worker                                    | PID 1    | `stop` took | Exit code |
| ------------------------------------------------------------ | -------- | ----------- | --------- |
| `["sh", "-c", "celery -A config --quiet worker; echo done"]` | `sh`     | 80.4 s      | 137       |
| `["sh", "-c", "celery -A config --quiet worker"]`            | `sh`     | 80.4 s      | 137       |
| `celery -A config --quiet worker` (the setting)              | `celery` | 2.2 s       | 0         |

Pulse was already on the right side of this. A `command:` string in Compose is
split into arguments, not handed to a shell, and the `Dockerfile` uses the JSON
form of `CMD`. The shell form to avoid is the Dockerfile's `CMD celery -A config
worker`, which Docker runs as `/bin/sh -c "..."`. To see what PID 1 is:

```bash
docker compose exec worker sh -c 'tr "\0" " " < /proc/1/cmdline'
```

### When the worker is killed anyway

A crash, an out-of-memory kill or a lost machine gives no grace period. Celery
acknowledges a message when a worker takes it, before running it, so the broker
considers a killed task done and never delivers it again:

- a `run_check` that was running is lost. The next interval's check replaces it,
  after the dead check's lock has expired (up to 90 s);
- a `send_alert` that was running leaves its alert `pending`, and nobody is told.

`task_acks_late` would acknowledge after the task returns instead. A killed
worker's message then stays unacknowledged, and Redis hands it out again once the
visibility timeout has passed, one hour by default. For `run_check` that changes
nothing: its message expires after `interval_s` and is dropped. For `send_alert`
the alert would go out, an hour late.

Late acknowledgement means a task can run twice: once before the kill and once
after redelivery. That is safe only for a task that is idempotent, which is why
D6 made `send_alert` stop unless its row is still `pending`, and why the receiver
drops a repeat by its `alert_id`. Pulse keeps the default and relies on the grace
period; why, and what would repair the lost alert better:
[ADR 0021](docs/adr/0021-early-ack-and-grace-period.md).

## flaky: a target you control

`flaky` is a tiny FastAPI app in `flaky/` that Pulse can monitor at
`http://flaky:8000/` (from inside Docker) or `http://localhost:8200/` (from your
laptop). Switch how it answers:

```bash
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "500"}'
```

| Mode   | `GET /` does                | Pulse reports                                                                           |
| ------ | --------------------------- | --------------------------------------------------------------------------------------- |
| `ok`   | 200                         | `ok: true`                                                                              |
| `500`  | 500                         | `wrong_status`                                                                          |
| `slow` | waits 10 s, then 200 (`"slow_seconds": 14` in the body changes the wait) | `timeout` (with `timeout_s` below the wait)              |
| down   | `docker compose stop flaky` | `dns`: a stopped container disappears from Docker's DNS, so the name no longer resolves |

`down` is deliberately not a mode: a crashed service doesn't answer politely, so
stopping the container is the realistic test. `docker compose start flaky` brings it back.

## shop: a target with real dependencies

`shop/` is a small Django app with its own Postgres (`shop-db`) and Redis
(`shop-redis`), run as a **separate Compose project** so it behaves like a service
someone else operates. Pulse reaches it as `http://shop:8000/` over the shared
`pulse-net` network; from your laptop it is http://localhost:8300.

| URL        | Depth   | Checks                                      | Down when                  |
| ---------- | ------- | ------------------------------------------- | -------------------------- |
| `/health`  | shallow | nothing: the process answers                 | the container is gone      |
| `/ht/`     | deep    | Postgres `SELECT 1`, cache set/get, Redis `PING` (django-health-check) | any component fails → 500 |
| `/`        | real    | one query against the catalogue             | Postgres is gone           |

With `Accept: application/json`, `/ht/` returns one key per component:

```json
{"Database(alias='default')": "OK", "Cache(alias='default')": "OK", "Redis(db=0, host='shop-redis', port=6379)": "OK"}
```

Two monitors show the difference. The deep one asserts on the two components
that matter and ignores the status code:

```bash
curl -X POST localhost:8100/monitors/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @- <<'EOF'
{"name": "shop shallow", "url": "http://shop:8000/health", "timeout_s": 5, "interval_s": 30}
EOF
curl -X POST localhost:8100/monitors/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @- <<'EOF'
{"name": "shop deep", "url": "http://shop:8000/ht/", "timeout_s": 5, "interval_s": 30,
 "assertions": {"Database(alias='default')": "OK", "Redis(db=0, host='shop-redis', port=6379)": "OK"}}
EOF
```

The Checkpoint 1 demo:

```bash
docker compose -f shop/compose.yaml stop shop-redis
curl -X POST -H "Authorization: Bearer $TOKEN" localhost:8100/monitors/<deep id>/check/      # ok: false, error_type: assertion_failed, status_code: 500
curl -X POST -H "Authorization: Bearer $TOKEN" localhost:8100/monitors/<shallow id>/check/   # ok: true
docker compose -f shop/compose.yaml start shop-redis
```

`docker compose -f shop/compose.yaml stop shop` takes both down; the shallow one
reports `connection_refused` or `dns`, depending on whether Docker still resolves the name.

## Networking

Inside a container, `localhost` means **that container**, not your laptop. So from
inside Pulse, `http://localhost:8000` is Pulse itself, not the shop that your
laptop reaches at `localhost:8300`.

Docker Compose gives each project its own network, and containers on the same
network reach each other by **service name** through Docker's built-in DNS. That is
why the `api` container reaches its database at `postgres:5432`. Pulse and `shop`
are separate projects, so they share one extra network, `pulse-net`, created once
by hand (`docker network create pulse-net`) and declared `external` in both files:

```yaml
# compose.yaml (Pulse)                 # shop/compose.yaml
services:                              services:
  api:                                   shop:
    networks: [default, monitoring]        networks: [default, monitoring]
networks:                              networks:
  monitoring:                            monitoring:
    name: pulse-net                        name: pulse-net
    external: true                         external: true
```

Only `api` and `shop` join it. `shop-db` and `shop-redis` stay on shop's own
network: Pulse cannot reach them directly, exactly like a real dependency behind
someone else's service, and only learns about them through `shop`'s `/ht/`.

Monitor URLs are validated with a custom validator because Django's `URLValidator`
rejects hostnames without a dot (such as `shop` or `flaky`).

## Configuration

All settings come from environment variables in `.env` (never committed).
`.env.example` lists every variable with placeholder values.

| Variable               | Description                                                           |
| ---------------------- | --------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`    | Secret used by Django for signing. Required.                          |
| `DJANGO_DEBUG`         | `true` for local development, `false` otherwise. Defaults to `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django will serve. Include `api` if Pulse monitors itself. |
| `WEB_CONCURRENCY`      | gunicorn worker processes in `api`. Defaults to `2`.                  |
| `PULSE_LOG_LEVEL`      | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, for every process. Defaults to `INFO`. |
| `PULSE_DEPENDENCY_TIMEOUT_S` | Seconds to wait for Postgres (any connection) or Redis (the `/ready` ping) before calling it unreachable. 1–10, default `2`. |
| `POSTGRES_DB`          | Database name.                                                        |
| `POSTGRES_USER`        | Database user.                                                        |
| `POSTGRES_PASSWORD`    | Database password.                                                    |
| `POSTGRES_HOST`        | Database host. `postgres` (the compose service name) inside Docker.   |
| `POSTGRES_PORT`        | Database port inside the Docker network. Defaults to `5432`.          |
| `PULSE_FAIL_THRESHOLD` | Failed checks in a row before an incident opens. 1–10, default `3`.   |
| `PULSE_OK_THRESHOLD`   | Successful checks in a row before it resolves. 1–10, default `2`.     |
| `CELERY_BROKER_URL`    | The queue. `redis://redis:6379/0` inside Docker. Required.            |
| `PULSE_WEBHOOK_URL`    | Where alerts are POSTed. Defaults to `http://catcher:8000/webhook`.   |
| `PULSE_WEBHOOK_SECRET` | Shared with the catcher; alerts are HMAC-signed with it. Required.    |
| `PULSE_ALERT_MAX_ATTEMPTS` | Delivery attempts per alert before it is marked failed. 1–10, default `5`. |
| `PULSE_WEBHOOK_TIMEOUT_S` | Seconds one delivery attempt may take. 1–30, default `5`. |
| `PULSE_REDIS_URL`      | Redis for the cache and the check locks. Defaults to `redis://redis:6379/1`, a different database from the broker's. |
| `PULSE_STATUS_CACHE_SECONDS` | Seconds `/status` is served from the cache. 0–300, default `15`; `0` turns the cache off. |
| `PULSE_JWT_SECRET`     | Signs access tokens. Required, at least 32 characters, different from `DJANGO_SECRET_KEY`. Changing it logs everyone out. |
| `PULSE_JWT_TTL_SECONDS` | Seconds a token is valid after `/login`. 60–86400, default `3600`. |
| `PULSE_ALLOWED_PRIVATE_HOSTS` | Comma-separated hostnames on private addresses that monitors may point at: your own targets, and `api` for Pulse itself (`flaky,shop,api`). Default: none. |
| `PULSE_RATE_LIMIT_PER_MINUTE` | Requests per minute from one client address, on the API. 1–6000, default `60`. |
| `PULSE_AUTH_RATE_LIMIT_PER_MINUTE` | The same, on `/login` and `/signup` together. 1–60, default `5`. |
| `PULSE_NUM_PROXIES`    | Proxies in front of Pulse that add to `X-Forwarded-For`. 0–5, default `0`: the header is ignored. |

Parsing is done by the typed readers in `config/env.py` (`env_str`, `env_bool`,
`env_int`, `env_list`): a blank value counts as unset, a boolean accepts only
`true`/`false`/`1`/`0`, and a value that can't be parsed fails at startup with the
variable's name. `tests/config/` covers them with Hypothesis.

## Troubleshooting

- **`port is already allocated`**: another program (often another Compose stack) is using
  8100 or 5433. Stop it, or change the host port in `compose.yaml`.
- **The app container keeps restarting or exits**: check `docker compose logs api`.
  A `KeyError` means a variable is missing from `.env`.
- **`migrate` fails after pulling D8** (`InconsistentMigrationHistory`, or
  `column "owner_id" ... contains null values`): the user model changed and
  monitors now need an owner, so a database from before D8 cannot be migrated.
  Reset it with `docker compose down -v`, start the stack and run `migrate` again.
- **A monitor on `http://api:8000/ready` reports `wrong_status` with 400**: `api`
  is missing from `DJANGO_ALLOWED_HOSTS`. `blocked` means it is missing from
  `PULSE_ALLOWED_PRIVATE_HOSTS`.
- **`denied` when pulling `ghcr.io/wbaghdadi20/pulse`**: the package is still
  private. See _Releases_.
- **`denied: permission_denied` in the release workflow**: the job that pushes
  needs `permissions: packages: write`.
- **CI fails on something that passes locally**: read the failing step's log. The
  usual cause is an environment variable, or a host that is `localhost` on the
  runner and a service name in Compose.
- **401 on every request**: the token expired (one hour by default). Log in again.
- **429 Too Many Requests**: the rate limit. `Retry-After` says how many seconds
  are left in the minute.
- **`password authentication failed for user`**: Postgres only applies `POSTGRES_PASSWORD`
  when its data volume is first created. If you changed the password in `.env` later,
  reset the database with `docker compose down -v` (deletes all data), then
  `docker compose up -d --wait` and run `migrate` again.

## Releases

A release is a version tag on `main`. Pushing the tag runs
`.github/workflows/release.yml`: the CI checks again, on the tagged commit, then
the image is built and pushed to GitHub's registry as
`ghcr.io/wbaghdadi20/pulse:<tag>` and `:latest`
([ADR 0020](docs/adr/0020-ci-and-releases.md)).

### Cutting one

```bash
git switch main && git pull
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

Tag `main`, after the pull request is merged. "Rebase and merge" gives the
commits new hashes, so a tag made on the branch names commits that never reach
`main`. Never move or reuse a tag: a published version would change under whoever
runs it. A bad release is followed by the next patch version.

### Which number

Versions are `MAJOR.MINOR.PATCH`
([semantic versioning](https://semver.org/)). Ask what someone running the
previous version has to do:

| They have to                                                            | Bump  | Examples                                                           |
| ----------------------------------------------------------------------- | ----- | ------------------------------------------------------------------ |
| nothing                                                                 | PATCH | a bug fix, a faster query, a dependency update                     |
| nothing, but there is something new to use                              | MINOR | a new endpoint, an optional field, a setting with a default        |
| change a client, their `.env`, their alert receiver, or reset their data | MAJOR | a removed or renamed field, a new required variable, a changed alert payload, a migration that cannot run on existing data |

While the version starts with `0.` nothing is promised stable yet, and a breaking
change bumps MINOR. A tag such as `v0.2.0-rc.1` is a pre-release: it is published
under its own name and `latest` does not move.

### Running one

`compose.release.yaml` replaces the `build:` of `api`, `worker` and `beat` with the
published image. `catcher` and `flaky` are test tools, not part of a release, and
are still built locally.

```bash
export PULSE_VERSION=v0.1.0
docker compose -f compose.yaml -f compose.release.yaml pull api worker beat
docker compose -f compose.yaml -f compose.release.yaml up -d --wait
docker compose -f compose.yaml -f compose.release.yaml exec api python manage.py migrate
docker compose -f compose.yaml -f compose.release.yaml images api   # which version is running
```

To roll back, set `PULSE_VERSION` to the previous tag and run `up -d` again. That
changes the code, not the database: migrations the newer version applied stay
applied.

A new package on ghcr.io is private, even when the repository is public. Until
its visibility is changed (the package's page on GitHub, _Package settings_,
_Change visibility_), pulling needs `docker login ghcr.io` with a token that has
the `read:packages` scope.

## Development

Install the git hooks once after cloning (requires [pre-commit](https://pre-commit.com/)):

    pre-commit install

Run all checks manually with `pre-commit run --all-files`.
The hooks run ruff and mypy (strict, with django-stubs). Run the type checker alone with `mypy`.

`compose.override.yaml` is merged automatically by Docker Compose. It builds the
image's `dev` stage, mounts the code into the `api` container and runs Django's
`runserver`, which reloads on save. To run the image that is published instead,
with gunicorn:

    docker compose -f compose.yaml up --build -d --wait

Passing -f compose.yaml tells Compose to use only that file, so the override isn't merged in.

### The image

One `Dockerfile`, two images
([ADR 0019](docs/adr/0019-image-stages-and-requirements.md)):

| Target    | Contains                                                         | Built by                                  |
| --------- | ---------------------------------------------------------------- | ----------------------------------------- |
| `runtime` | the packages in `requirements.txt` and the four app packages     | `compose.yaml`, CI, the release           |
| `dev`     | the same, plus `requirements-dev.txt` and the whole repository   | `compose.override.yaml`                   |

A `builder` stage installs the runtime packages into `/venv`; `runtime` copies
that one directory and the code onto a clean `python:3.14-slim`. Both run as the
user `app`, not root (`docker compose exec api whoami`), and `api`, `worker` and
`beat` are the same image started with different commands.

| `docker images`                         | On disk | Compressed |
| --------------------------------------- | ------- | ---------- |
| before: one stage, everything installed | 541 MB  | 106 MB     |
| `runtime`                               | 386 MB  | 73.8 MB    |
| `dev`                                   | 595 MB  | 121 MB     |

The saving is the test, type and lint tools (mypy alone is 61 MB) and the files
that are no longer copied. Every dependency installs as a ready-made wheel, so the
builder stage has no compiler to leave behind.

A new dependency goes into one of two files, each pinned:

```bash
pip install <package>
pip freeze            # copy the new lines into the file they belong to
```

`requirements.txt` if the app imports it, `requirements-dev.txt` (which starts
with `-r requirements.txt`) if only tests or tools do. The tests cannot notice a
package on the wrong side, because they run with both installed; CI's last step
runs the built image for that reason.

### Continuous integration

`.github/workflows/ci.yml` runs on every pull request and every push to `main`
([ADR 0020](docs/adr/0020-ci-and-releases.md)). One job, in this order, stopping
at the first failure:

| Step                         | Runs                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| Lint (ruff)                  | `ruff check .` and `ruff format --check .`                           |
| Types (mypy)                 | `mypy`                                                               |
| Migrations match the models  | `manage.py makemigrations --check --dry-run`                         |
| Tests (pytest)               | `pytest -n auto --cov`, with `HYPOTHESIS_PROFILE=ci`                 |
| Image builds (docker build)  | `docker build .`                                                     |
| Image works                  | migrate, start the API and a worker from that image, `/ready`, sign up, log in, ping the worker |

Postgres and Redis run as service containers. The steps run on the runner itself,
where those are on `localhost` and not under their Compose names; the workflow sets
`POSTGRES_HOST`, `CELERY_BROKER_URL` and `PULSE_REDIS_URL` accordingly. A second
job runs the catcher's tests. The check `ci` is required on `main`: a red pull
request cannot be merged.

To reproduce a CI failure, read the failing step's log in the pull request's
_Checks_ tab. When it passes locally and fails there, the difference is nearly
always an environment variable or a host name.

### Tests

```bash
docker compose exec api pytest            # run everything (~8 s)
docker compose exec api pytest -n auto    # the same on every core, one test database per worker
docker compose exec api pytest --cov      # with a coverage report
docker compose exec api pytest --create-db  # after a new migration (tests reuse the DB otherwise)
docker compose exec api pytest tests/monitors/test_incidents.py -v  # one file
docker compose exec api pytest -m "not django_db"  # only the pure tests, no database (~0.05 s)
docker compose exec api pytest tests/monitors/test_incidents_properties.py --hypothesis-show-statistics  # how often each branch ran
docker compose exec api env HYPOTHESIS_PROFILE=ci pytest   # 2000 examples per property instead of 500
docker compose run --rm catcher pytest -q  # the catcher's own tests (signature verification)
```

Tests live in a root-level `tests/` package, one sub-package per Django app
(`tests/monitors/`, `tests/core/`, `tests/accounts/`), with the shared fixtures in
`tests/conftest.py` so every app can use them: `api` (a client logged in as
`user`, with a token issued directly instead of through `/login`), `anon` (no
token), `monitor`, `fake_check`, and `dns`, which replaces the resolver for every
test so none looks a name up for real. Keeping tests out of
the app packages also keeps them out of the coverage report's `source`.

Locally, tests run inside the `api` container because they need the same
environment and database host as the app. CI runs the same suite on its runner,
with Postgres and Redis on `localhost` (see _Continuous integration_). `pytest-django` creates a separate `test_pulse` database
and wraps each test in a transaction that is rolled back, so tests never touch
development data and always start empty.

The suite is safe to run in parallel (`-n auto`, pytest-xdist). Each worker
process gets its own Postgres database from pytest-django (`test_pulse_gw0`,
`test_pulse_gw1`, ...) and its own in-process cache. The Redis tests share one
Redis database, so the `redis_db` fixture holds a file lock for the length of a
test and the workers take turns. After a new migration, pass `--create-db` once.
With a suite this small the gain is modest: about 5 s with 8 workers against 8 s
serially, and no faster with 16, because starting the workers costs what they save.

The kinds of tests, fastest first:

| Where                                                                | What                                                                        | Needs             |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------- | ----------------- |
| `tests/monitors/test_incidents.py`, `test_validators.py`             | pure logic                                                                  | nothing           |
| `tests/monitors/test_incidents_properties.py`                        | Hypothesis: random up/down sequences through `decide()`, four invariants    | nothing           |
| `tests/monitors/test_checks.py`                                      | `probe()` against `httpx.MockTransport`: every `error_type`, no network     | nothing           |
| `tests/monitors/test_clients.py`                                     | the two shared httpx clients against a loopback server that counts connections | nothing        |
| `tests/monitors/test_api.py`, `test_incident_flow.py`, `test_status_page.py`, `tests/core/` | the full stack through DRF's `APIClient`             | the test database |
| `tests/monitors/test_claim.py`                                       | claiming due monitors, including a second connection that must not wait      | the test database |
| `tests/monitors/test_locks.py`, `test_tasks.py`                      | the check lock against a real Redis (database 15, emptied around each test)  | Redis             |

Two things are injectable so tests can control them: `probe(monitor, client=...)`
takes an httpx client (tests pass a fake transport; production passes nothing and
gets the process's shared client from `monitors/clients.py`), and
`get_uptime(..., now=...)` takes the current time. The database tests are the ones marked `django_db`, so
`-m "not django_db"` is the unit/integration switch: no extra markers to maintain.
Coverage is configured in `pyproject.toml`; the suite sits at ~97%.

### Mutation testing

Coverage says a line ran; it doesn't say a test would notice if the line were
wrong. [mutmut](https://mutmut.readthedocs.io/) changes the code on purpose
(`<` to `<=`, a constant to `None`, an argument dropped) and runs the tests against
each mutant. A mutant the tests don't kill is a gap, or an *equivalent mutant*
(no observable change, e.g. `is` to `==` on booleans) that no test can kill.

```bash
docker compose exec api mutmut run        # a few minutes; results persist in mutants/
docker compose exec api mutmut results    # the survivors
docker compose exec api mutmut show <id>  # the diff of one mutant
```

Configuration is in `pyproject.toml`. `monitors/incidents.py` on its own had **27
mutants, 0 survivors**, so the scope is the whole `monitors/` and `core/` source
(migrations, models, admin and apps excluded). Scores on D5:

| Run                                   | Mutants | Survived | Killed |
| ------------------------------------- | ------- | -------- | ------ |
| baseline                              | 392     | 63       | 84%    |
| after 7 new tests                     | 392     | 45       | 89%    |
| after the JSON-assertion feature too  | 436     | 54       | 88%    |
| D6: Celery tasks and alerts added     | 547     | 76       | 86%    |
| D6: after pinning the alert payload   | 547     | 63       | 88%    |
| D9: everything since, `accounts/` too | 1447    | 120      | 92%    |

The 13 D6 survivors killed by one test were the alert payload keys and the
`Content-Type` header: the webhook contract was sent but never asserted. The 8 that
remain in `alerting.deliver` are equivalent mutants (`sort_keys`, the JSON separators,
`refresh_from_db` field list): they change bytes that are signed and verified
consistently either way, so no behaviour a test could observe. `send_alert` itself has
no survivors.

The 18 killed survivors were real gaps, each now a test: the uptime and incident
queries were never checked to filter by monitor (`test_uptime_counts_only_this_monitors_checks`,
`test_incidents_are_per_monitor`), the probe's no-client branch and the timeout
it sends were untested, latency was never asserted to be in milliseconds, the 409
messages and the incident `cause` were never read, and `PATCH` with invalid input
had no test. Most of what survives is error-message wording, `output_field` on an
aggregate Django infers anyway, and `update_fields` lists: equivalent or not worth
a test. The remaining ones to look at are in `validate_assertions`.

mutmut runs the tests from a copy in `mutants/`, which is why `pyproject.toml` pins
pytest's import path to the rootdir (`pythonpath`, `django_find_project = false`):
without that, pytest-django puts the real package first on `sys.path` and every
mutant silently "survives" untested. See DEBUGGING.md, bug 3.
