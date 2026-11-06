# Pulse

Pulse is an uptime monitor and status page. It checks HTTP services on a schedule,
records every result, opens and resolves incidents, sends signed alerts to a webhook,
and serves a public status page.

Built with Django, PostgreSQL and Docker Compose.

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

3. Generate a secret key and paste it into `.env` as `DJANGO_SECRET_KEY`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   Also change `POSTGRES_PASSWORD` to a password of your choice.

4. Build and start the stack:

   ```bash
   docker compose up --build -d --wait
   ```

5. Create the database tables:

   ```bash
   docker compose exec api python manage.py migrate
   ```

6. Check it works:

   ```bash
   curl -i http://localhost:8100/health
   ```

   Expected: `HTTP/1.1 200 OK` with `{"status": "ok"}`.

## Services and ports

| Service    | Description           | URL / port on your machine                 |
| ---------- | --------------------- | ------------------------------------------ |
| `api`      | Django app            | http://localhost:8100                      |
| `postgres` | PostgreSQL 16         | `localhost:5433` (bound to 127.0.0.1 only) |
| `flaky`    | test target (FastAPI) | http://localhost:8200                      |

Host ports 8100 / 8200 / 5433 are used instead of 8000 / 5432 so Pulse can run
alongside other local stacks.

## Everyday commands

| Command                                              | What it does                                     |
| ---------------------------------------------------- | ------------------------------------------------ |
| `docker compose up --build -d --wait`                | Build and start everything in the background     |
| `docker compose ps`                                  | Show running services and their health           |
| `docker compose logs -f api`                         | Follow the app's logs (Ctrl+C stops following)   |
| `docker compose exec api python manage.py <command>` | Run a Django management command                  |
| `docker compose down`                                | Stop everything (database data is kept)          |
| `docker compose down -v`                             | Stop everything **and delete the database data** |

## API

Built with Django REST framework. Open any endpoint in a browser for DRF's
browsable API.

| Method + URL                   | Does                         | Success | Errors   |
| ------------------------------ | ---------------------------- | ------- | -------- |
| `GET /health`                  | liveness check               | 200     |          |
| `POST /monitors/`              | create a monitor             | 201     | 400, 409 |
| `GET /monitors/?limit&offset`  | list monitors (paginated)    | 200     |          |
| `GET /monitors/<id>/`          | get one monitor              | 200     | 404      |
| `PATCH /monitors/<id>/`        | change some fields           | 200     | 400, 404, 409 |
| `DELETE /monitors/<id>/`       | delete a monitor             | 204     | 404      |
| `POST /monitors/<id>/check/`   | call the monitor's URL once and record the result | 200 | 404 |
| `GET /monitors/<id>/uptime/?days=7\|30\|90` | uptime %, p95 latency and check count | 200 | 400, 404 |
| `POST /monitors/<id>/incidents/` | open an incident by hand   | 201     | 404, 409 |

Example:

```bash
curl -X POST http://localhost:8100/monitors/ -H 'Content-Type: application/json' \
  -d '{"name": "httpbin", "url": "https://httpbin.org/get", "timeout_s": 5, "interval_s": 30}'
```

Conventions:

- **Invalid input returns 400** with one message per field. That is DRF's
  convention (FastAPI uses 422).
- **URLs end with a slash** (`/monitors/`), the Django convention. A `POST` without
  the slash fails, because Django can't redirect a `POST` and keep its body.
- **Pagination** uses `?limit=` (default 20, max 100) and `?offset=`. The response is
  `{"count": <total>, "next": <url>, "previous": <url>, "results": [...]}`.
- **"Check now" always returns 200.** A target that is down is a result, not an
  error in Pulse. `error_type` is one of `timeout`, `connection_refused`, `dns`,
  `wrong_status`, or `error` (anything else, e.g. a TLS failure).

### Incidents and flap protection

Every check result feeds a small state machine (`monitors/incidents.py`):

- No open incident and the last **N** checks all failed → open an incident.
- Open incident and the last **M** checks all passed → resolve it.
- Anything else (a single failed check between successes, not enough checks yet)
  → nothing. Networks blip; alerting on every blip trains people to ignore alerts.
- A monitor never has two open incidents: the state machine won't ask for one, and
  the database forbids it anyway (see *Rules the database enforces*).

N and M are settings: `PULSE_FAIL_THRESHOLD` (default 3) and `PULSE_OK_THRESHOLD`
(default 2). The decision function is pure (no Django, no database) and takes the
recent results plus the current state; `services.evaluate_incidents()` loads those
from Postgres, calls it, and applies the answer.

### Which endpoints are idempotent

Idempotent means calling it twice leaves the system in the same state as calling it once.

| Endpoint             | Idempotent? | Why |
| -------------------- | ----------- | --- |
| `GET` (all)          | yes         | reads only, changes nothing |
| `PATCH /monitors/<id>/` | yes      | it sets fields to values; setting `timeout_s` to 10 twice still leaves 10 |
| `DELETE /monitors/<id>/` | yes     | after the first call the monitor is gone, and a second call changes nothing. The *response* differs (204, then 404), but idempotency is about the effect, not the response |
| `POST /monitors/`    | no          | each call tries to create a new monitor (the second one gets 409 only because names are unique) |
| `POST /monitors/<id>/check/` | no  | each call makes a new request to the target and records a new result |

## Database

Postgres, through the Django ORM. Tables: `monitors`, `checks` (one row per check
result) and `incidents` (one row per outage; `resolved_at` is empty while it's open).

| Command (prefix with `docker compose exec api`) | What it does |
| ------------------------------------------------ | ------------ |
| `python manage.py migrate`                        | apply all migrations (on an empty database: creates every table and index) |
| `python manage.py makemigrations`                 | generate a migration after changing `models.py` |
| `python manage.py sqlmigrate monitors 0002`       | show the SQL a migration will run |
| `python manage.py seed_checks`                    | insert 500,000 fake checks for 10 `seed-N` monitors over the last 90 days (`--count`, `--monitors` to change) |

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

### Rules the database enforces

| Rule | Constraint | Violation → API |
| ---- | ---------- | --------------- |
| Monitor names are unique | `uq_monitor_name` (unique) | 409 |
| One open incident per monitor | `uq_one_open_incident`: partial unique index on `monitor_id` `WHERE resolved_at IS NULL` | 409 |

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

## flaky: a target you control

`flaky` is a tiny FastAPI app in `flaky/` that Pulse can monitor at
`http://flaky:8000/` (from inside Docker) or `http://localhost:8200/` (from your
laptop). Switch how it answers:

```bash
curl -X POST localhost:8200/mode -H 'Content-Type: application/json' -d '{"mode": "500"}'
```

| Mode   | `GET /` does                | Pulse reports        |
| ------ | --------------------------- | -------------------- |
| `ok`   | 200                         | `ok: true`           |
| `500`  | 500                         | `wrong_status`       |
| `slow` | waits 10 s, then 200        | `timeout` (with `timeout_s` < 10) |
| down   | `docker compose stop flaky` | `dns`: a stopped container disappears from Docker's DNS, so the name no longer resolves |

`down` is deliberately not a mode: a crashed service doesn't answer politely, so
stopping the container is the realistic test. `docker compose start flaky` brings it back.

## Networking

Inside a container, `localhost` means **that container**, not your laptop. So from
inside Pulse, `http://localhost:8000` is Pulse itself, not the EasyeSuite backend
that your laptop reaches at `localhost:8000`.

Docker Compose gives each project its own network, and containers on the same
network reach each other by **service name** through Docker's built-in DNS. That is
why the `api` container reaches its database at `postgres:5432`. To monitor
EasyeSuite, the `api` service joins EasyeSuite's network as an external network.
Then `http://django:8000/ht/` and `http://frontend:3000/` resolve to those
containers:

```yaml
services:
  api:
    networks: [default, easyesuite]
networks:
  easyesuite:
    external: true
    name: <name from `docker network ls`>
```

Monitor URLs are validated with a custom validator because Django's `URLValidator`
rejects hostnames without a dot (such as `django` or `frontend`).

## Configuration

All settings come from environment variables in `.env` (never committed).
`.env.example` lists every variable with placeholder values.

| Variable               | Description                                                           |
| ---------------------- | --------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`    | Secret used by Django for signing. Required.                          |
| `DJANGO_DEBUG`         | `true` for local development, `false` otherwise. Defaults to `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django will serve.                          |
| `POSTGRES_DB`          | Database name.                                                        |
| `POSTGRES_USER`        | Database user.                                                        |
| `POSTGRES_PASSWORD`    | Database password.                                                    |
| `POSTGRES_HOST`        | Database host. `postgres` (the compose service name) inside Docker.   |
| `POSTGRES_PORT`        | Database port inside the Docker network. Defaults to `5432`.          |
| `PULSE_FAIL_THRESHOLD` | Failed checks in a row before an incident opens. Defaults to `3`.     |
| `PULSE_OK_THRESHOLD`   | Successful checks in a row before it resolves. Defaults to `2`.       |

## Troubleshooting

- **`port is already allocated`**: another program (often the EasyeSuite stack) is using
  8100 or 5433. Stop it, or change the host port in `compose.yaml`.
- **The app container keeps restarting or exits**: check `docker compose logs api`.
  A `KeyError` means a variable is missing from `.env`.
- **`password authentication failed for user`**: Postgres only applies `POSTGRES_PASSWORD`
  when its data volume is first created. If you changed the password in `.env` later,
  reset the database with `docker compose down -v` (deletes all data), then
  `docker compose up -d --wait` and run `migrate` again.

## Development

Install the git hooks once after cloning (requires [pre-commit](https://pre-commit.com/)):

    pre-commit install

Run all checks manually with `pre-commit run --all-files`.

`compose.override.yaml` is merged automatically by Docker Compose. It mounts the
code into the `api` container and runs Django's `runserver`, which reloads on save.
To run the production-style gunicorn setup instead:

    docker compose -f compose.yaml up --build -d --wait

Passing -f compose.yaml tells Compose to use only that file, so the override isn't merged in.

### Tests

```bash
docker compose exec api pytest            # run everything (~1 s)
docker compose exec api pytest --cov      # with a coverage report
docker compose exec api pytest --create-db  # after a new migration (tests reuse the DB otherwise)
docker compose exec api pytest tests/monitors/test_incidents.py -v  # one file
docker compose exec api pytest -m "not django_db"  # only the pure tests, no database (~0.05 s)
```

Tests live in a root-level `tests/` package, one sub-package per Django app
(`tests/monitors/`, `tests/core/`), with the shared fixtures (`api`, `monitor`,
`fake_check`) in `tests/conftest.py` so every app can use them. Keeping tests out of
the app packages also keeps them out of the coverage report's `source`.

Tests run inside the `api` container because they need the same environment and
database host as the app. `pytest-django` creates a separate `test_pulse` database
and wraps each test in a transaction that is rolled back, so tests never touch
development data and always start empty.

Three kinds of tests, fastest first:

| Where | What | Needs |
| ----- | ---- | ----- |
| `tests/monitors/test_incidents.py`, `test_validators.py` | pure logic | nothing |
| `tests/monitors/test_checks.py` | `run_check()` against `httpx.MockTransport`: every `error_type`, no network | nothing |
| `tests/monitors/test_api.py`, `test_incident_flow.py`, `tests/core/` | the full stack through DRF's `APIClient` | the test database |

Two things are injectable so tests can control them: `run_check(monitor, client=...)`
takes an httpx client (tests pass a fake transport), and `get_uptime(..., now=...)`
takes the current time. The database tests are the ones marked `django_db`, so
`-m "not django_db"` is the unit/integration switch: no extra markers to maintain.
Coverage is configured in `pyproject.toml`; the suite sits at ~97%.
