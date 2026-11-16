# Load test

How much load Pulse takes, what limited it, and what one fix changed.

**Result.** With 50 concurrent users and 4 gunicorn workers Pulse served about
475 requests a second with no errors. The slowest endpoint was the uptime
query: each request made Postgres read about 8,200 pages to aggregate 8,600
rows. A covering index turned that into an index-only scan over about 50 pages.
The same test then ran at about 730 requests a second, and the p95 of the
uptime endpoint went from about 185 ms to about 100 ms.

## Setup

| | |
| --- | --- |
| Machine | AMD Ryzen 7 9700X (8 cores, 16 threads), 23 GB, Docker 29.9 in WSL2 |
| Pulse | the `runtime` image from the `Dockerfile`, gunicorn 26.2 with 4 sync workers, `DJANGO_DEBUG=false` |
| Services | `api`, `postgres` (16, default settings, `shared_buffers` 128 MB) and `redis`. `worker` and `beat` are stopped, so nothing writes while a test runs |
| Load | Locust 2.47, on the same machine, `-u 50 -r 5 -t 2m` against `http://localhost:8100` |

**The data.** A separate database, `pulse_loadtest`, filled by `loadtest/seed.py`:
50 accounts with 10 monitors each, a check every 5 minutes for 30 days (90 days
for the 20 public monitors), 3 past incidents per public monitor. 4,666,100
checks, 547 MB with the index. The checks are inserted in time order, so one
monitor's rows are scattered among the others', as they are when Pulse writes
them itself. Seeded monitor by monitor they would sit on neighbouring pages and
every query for one monitor would look cheaper than it is.

**The load** (`loadtest/locustfile.py`). Each Locust user logs in once as its
own account, then:

| Share | Request | |
| --- | --- | --- |
| 70% | `GET /status` | public, sent without a token |
| 20% | `GET /monitors/` | the account's 10 monitors |
| 10% | `GET /monitors/<id>/uptime/?days=30` | one of its own monitors, picked at random |

There is no pause between requests: a user sends the next one when the last one
is answered. Fifty users are fifty requests in flight, so requests per second
is what Pulse can serve, not how fast the users click.

**The rate limiter was turned up for these runs, and it was still on.** All
Locust users share one address, and the default of 60 requests a minute per
address would have answered 429 to nearly everything. `loadtest/compose.loadtest.yaml`
sets `PULSE_RATE_LIMIT_PER_MINUTE=6000` (the highest value Pulse accepts) and
`PULSE_NUM_PROXIES=1`, and each Locust user sends its own `X-Forwarded-For`
address, so each is counted as its own client. Every request still pays for the
limiter's Redis call. Both values are overrides in that Compose file only: `.env`
is not edited, and the normal stack runs with the defaults.

**Each configuration was run twice.** Tables show both runs as `first / second`.
Locust rounds percentiles above 100 ms to the nearest 10.

### Running it

Typed in the repository's root folder, in this order. Each `make` target prints
the commands it runs.

**1. Switch Pulse to load-test mode.**

```bash
make loadtest-up
```

It stops `worker` and `beat` and replaces `api` with the runtime image under
gunicorn, on the database `pulse_loadtest`. **From now until step 6, Pulse runs
no checks and sends no alerts.**

**2. Create and fill the load-test database.** Needed once per machine. Run
again, it finds the data and changes nothing.

```bash
make loadtest-seed
```

It ends with `50 accounts, 500 monitors (20 public), 4666100 checks, 120 incidents`,
or with `Already seeded.`

**3. Run the test.** Two minutes. The first time it installs Locust first.

```bash
make loadtest
```

The table at the end has requests per second, the percentiles and the failures
per endpoint. Other numbers: `make loadtest USERS=100 RATE=10 DURATION=5m`.

**4. Optional: another number of gunicorn workers**, then step 3 again.
`{1|2|4|8|16}` are the values measured here; type one.

```bash
make loadtest-up WORKERS=8
make loadtest
```

**5. Optional: look at the query plan** of the uptime query on the seeded data.
_Finding the bottleneck_ below says how to read it.

```bash
make explain DB=pulse_loadtest MONITOR=123 DAYS=30
```

**6. Always last: back to development.** This undoes step 1: `api` is the
development server again, and `worker` and `beat` start.

```bash
make loadtest-down
```

If step 6 is forgotten, nothing is checked and no incident can open. `make
demo-ready` says so: `FAIL  api is in load-test mode. Run: make loadtest-down`.

Steps 2 and 3 first ask the running `api` container which database it uses, and
stop with `First: make loadtest-up` if it is not `pulse_loadtest`. The database
`pulse_loadtest` is not deleted by any of this; _The same by hand_ ends with how.

### The same by hand

What those targets do, for a machine without `make` or for reading. Every command
is typed on your machine, in the repository's root folder. Nothing is abbreviated:
each line can be pasted as it is. The steps here are numbered on their own.

How to read them:

- `docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml` means "Pulse
  as described by these two files, the second on top of the first". Naming the
  files also switches `compose.override.yaml` off, and that file is what turns
  `api` into the development server. Plain `docker compose` (no `-f`) is the
  development stack. For `up`, which creates containers, the difference decides
  what gets created. For `exec`, `logs` and `stop`, which act on containers that
  exist, both forms reach the same containers.
- `exec <service> <command>` runs `<command>` inside a container that is already
  running. `{api|worker|beat}` in a command means any one of those services
  works there, and you type one of them.
- `up -d --wait` starts containers in the background (`-d`) and returns once they
  are running and healthy (`--wait`). `--build` builds the image first.

**1. Stop what would write to the database during a test.** `worker` runs the
checks and `beat` schedules them. Skip this if they are not running.

```bash
docker compose stop worker beat
```

**2. Start Pulse as it is tested.** This rebuilds `api` as the `runtime` image and
replaces the running `api` container with one that runs gunicorn with 4 workers,
`DJANGO_DEBUG=false`, and the database name `pulse_loadtest`. `postgres` and
`redis` are started if they are not running.

```bash
docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml up -d --build --wait api
```

**3. Create the empty database** (once). This runs `createdb` inside the
`postgres` container. The single quotes matter: they make `$POSTGRES_USER` be
read inside the container, where it is set, and not on your machine, where it is
not.

```bash
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" pulse_loadtest'
```

**4. Create the tables in it** (once). `manage.py migrate` runs inside the `api`
container, which is the one container pointed at `pulse_loadtest`. Run from
`worker` or `beat` it would migrate the development database.

```bash
docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml exec api python manage.py migrate
```

**5. Fill it** (once, about 30 seconds). `python manage.py shell` is Django's
Python prompt, inside the `api` container. `< loadtest/seed.py` feeds it the file
from your machine as if it were typed in, so the script runs with Django set up
and the image does not need to contain it. `-T` tells Docker there is no keyboard
attached: without it the command fails with "the input device is not a TTY".
Again `api` only.

```bash
docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml exec -T api python manage.py shell < loadtest/seed.py
```

It prints `50 accounts, 500 monitors (20 public), 4666100 checks, 120 incidents`.
Run a second time it prints `Already seeded.` and changes nothing. Run against
any database but `pulse_loadtest` it prints `Not the load-test database` and
writes nothing.

**6. Install Locust** (once). It goes into its own virtualenv in `loadtest/.venv`,
on your machine and not in a container.

```bash
python3 -m venv loadtest/.venv
loadtest/.venv/bin/pip install -r loadtest/requirements.txt
```

**7. Run the test.** `-f` is the file that describes the users, `--headless`
means no web interface, `-u 50` is fifty users, `-r 5` starts five of them a
second, `-t 2m` stops after two minutes, `--host` is where the requests go: the
port `api` publishes on your machine.

```bash
loadtest/.venv/bin/locust -f loadtest/locustfile.py --headless -u 50 -r 5 -t 2m --host http://localhost:8100
```

The table it prints at the end has requests per second, the percentiles and the
failures per endpoint. In a second terminal, while it runs:

```bash
docker stats pulse-api-1 pulse-postgres-1 pulse-redis-1
```

**8. Another number of workers.** Same command as step 2 with `WEB_CONCURRENCY`
set in front, which replaces the `api` container with one running that many
gunicorn workers. `{1|2|4|8|16}` are the values measured here. Then step 7 again.

```bash
WEB_CONCURRENCY=8 docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml up -d --wait api
```

**9. Look at a query plan.** This opens `psql` inside the `postgres` container on
the load-test database. Paste a query after `EXPLAIN (ANALYZE, BUFFERS)`, and
leave with `\q`.

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d pulse_loadtest'
```

**10. Back to the development stack.** Without `-f`, `compose.override.yaml`
applies again: `api` is rebuilt as the development image and replaced, and
`worker` and `beat` start. The load-test database stays where it is.

```bash
docker compose up -d --build --wait
```

To delete the load-test database afterwards (about 550 MB):

```bash
docker compose exec postgres sh -c 'dropdb -U "$POSTGRES_USER" pulse_loadtest'
```

## Baseline

| Endpoint | Requests/s | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
| --- | --- | --- | --- | --- | --- |
| `GET /status` | 337 / 326 | 91 / 95 | 140 / 140 | 170 / 180 | 0 |
| `GET /monitors/` | 98 / 92 | 100 / 110 | 150 / 150 | 180 / 200 | 0 |
| `GET /monitors/<id>/uptime/?days=30` | 48 / 47 | 130 / 140 | 180 / 190 | 210 / 230 | 0 |
| **All** | **484 / 465** | 97 / 100 | 150 / 160 | 190 / 200 | **0** |

57,582 and 55,382 requests. The 50 logins at the start are not in the mix: they
took about 140 ms at p50, most of it the Argon2 password hash doing its job.

## Finding the bottleneck

The slowest endpoint by p95 is uptime. But the three endpoints are oddly close:
a status page that is one Redis read should not take 91 ms.

### 1. Where the time goes: waiting, not working

D9's request log has a `duration_ms` for every request, measured inside Django.
Next to what Locust measured from outside (first baseline run, p50):

| Endpoint | Inside Django | Seen by the client | Difference |
| --- | --- | --- | --- |
| `GET /status` | 1 ms | 91 ms | 90 ms |
| `GET /monitors/` | 12 ms | 100 ms | 88 ms |
| `GET /monitors/<id>/uptime/` | 42 ms | 130 ms | 88 ms |

Every request waits about 90 ms before a worker picks it up. Four sync workers
serve four requests at a time, and the other 46 wait in line. So what limits
throughput is how long the workers are busy, and the question becomes which
requests keep them busy. Per 100 requests of the mix:

| Endpoint | Requests | Time each | Worker time | Share |
| --- | --- | --- | --- | --- |
| `GET /status` | 70 | 1 ms | 70 ms | 9% |
| `GET /monitors/` | 20 | 12 ms | 240 ms | 32% |
| `GET /monitors/<id>/uptime/` | 10 | 43 ms | 430 ms | 58% |

One request in ten takes more than half of all worker time. Everyone else's
p95 is mostly time spent queueing behind uptime requests.

### 2. Not an N+1

The SQL each endpoint runs, counted with Django's `CaptureQueriesContext`:

| Endpoint | Queries | |
| --- | --- | --- |
| `GET /status` | 0 | served from the cache; 4 when the cache is empty |
| `GET /monitors/` | 3 | the token's user, the count, the page |
| `GET /monitors/<id>/uptime/` | 3 | the token's user, the monitor, the aggregate |

None of these grows with the number of monitors or checks. The list was an N+1
once and was fixed on D3 ([ADR 0012](docs/adr/0012-latest-check-without-n-plus-one.md)).
Tests hold all three numbers (`tests/monitors/test_latest_check.py`,
`test_status_page.py`, `test_uptime_query.py`).

Timed alone on an idle database, the uptime request's three queries took 1 ms,
under 1 ms and 7 ms. It is one query, not many.

### 3. `EXPLAIN (ANALYZE, BUFFERS)` on that query

```
Aggregate  (actual time=57.025..57.027 rows=1 loops=1)
  Buffers: shared hit=3080 read=5136
  ->  Bitmap Heap Scan on checks  (actual time=5.327..54.188 rows=8639 loops=1)
        Recheck Cond: ((monitor_id = 123) AND (checked_at > (now() - '30 days'::interval)))
        Heap Blocks: exact=8143
        Buffers: shared hit=3072 read=5136
        ->  Bitmap Index Scan on checks_monitor_checked_idx  (actual time=2.893..2.894 rows=8639 loops=1)
              Index Cond: ((monitor_id = 123) AND (checked_at > (now() - '30 days'::interval)))
              Buffers: shared hit=4 read=61
Execution Time: 57.198 ms
```

The index does its part in 3 ms: 8,639 matching entries on 61 pages. Then
Postgres fetches each row from the table to read `ok` and `latency_ms`, and the
8,639 rows are on 8,143 different pages, because a monitor's checks arrive five
minutes apart with every other monitor's checks in between. That is 64 MB of
table read to produce three numbers. `shared_buffers` is 128 MB, so two
concurrent uptime requests push each other's pages out (`read=5136`: most pages
were not in Postgres's cache).

### 4. `docker stats` during the run

| Container | CPU, baseline (average of 59 samples per run) |
| --- | --- |
| `postgres` | 273% / 276% |
| `api` | 184% / 181% |
| `redis` | 7% / 7% |

Postgres uses more CPU than the four API workers together. The workers are
mostly waiting for it.

**The bottleneck:** the uptime aggregate reads one table page per check.

## The fix

One change: the index on `checks (monitor_id, checked_at)` now also carries
`ok` and `latency_ms` (`INCLUDE`), the only two columns the query aggregates,
and the query counts `*` instead of `id`. Postgres can then answer from the
index alone, where one monitor's entries are next to each other
(migration `0011`, [ADR 0022](docs/adr/0022-uptime-covering-index.md)).

```
Aggregate  (actual time=1.463..1.464 rows=1 loops=1)
  Buffers: shared hit=11 read=50
  ->  Index Only Scan using checks_monitor_checked_cover on checks  (actual time=0.052..0.628 rows=8639 loops=1)
        Index Cond: ((monitor_id = 123) AND (checked_at > (now() - '30 days'::interval)))
        Heap Fetches: 2
        Buffers: shared hit=3 read=50
Execution Time: 1.519 ms
```

8,216 pages became 61, and 57 ms became 1.5 ms.

## After

The same test, the same data, the same 4 workers. Nothing else changed.

| Endpoint | Requests/s before | after | p50 before (ms) | after | p95 before (ms) | after | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `GET /status` | 337 / 326 | 491 / 528 | 91 / 95 | 64 / 60 | 140 / 140 | 93 / 80 | 0 |
| `GET /monitors/` | 98 / 92 | 142 / 150 | 100 / 110 | 75 / 71 | 150 / 150 | 110 / 92 | 0 |
| `GET /monitors/<id>/uptime/?days=30` | 48 / 47 | 70 / 75 | 130 / 140 | 76 / 72 | 180 / 190 | 110 / 93 | 0 |
| **All** | **484 / 465** | **703 / 754** | 97 / 100 | 67 / 63 | 150 / 160 | 100 / 86 | **0** |

p99 over all requests went from 190 / 200 ms to 130 / 100 ms.

| | Before | After |
| --- | --- | --- |
| Uptime request inside Django, p50 | 42 / 44 ms | 13 / 13 ms |
| Uptime request inside Django, p95 | 53 / 55 ms | 18 / 16 ms |
| `postgres` CPU | 273% / 276% | 155% / 157% |
| `api` CPU | 184% / 181% | 277% / 285% |

About 54% more requests a second. The status page and the monitor list got
faster without being touched, because they wait in a shorter line. The worker
time per 100 requests fell from 740 ms to about 440 ms, which predicts 68% more
throughput. The rest went to the next limit: the API's own CPU, now above
Postgres's.

Two things the after numbers do not show:

- **The table was freshly vacuumed and nothing wrote to it.** An index-only
  scan skips the table only for pages Postgres knows are visible to everyone,
  which `VACUUM` records. Rows written since the last vacuum are still fetched
  from the table. Measured: with one more day of checks inserted and no
  vacuum, the same query fetched that day's 290 rows from the table and read
  343 pages instead of 50, still in 1.4 ms. By default autovacuum visits a
  table that only receives inserts once the new rows reach 20% of it, here six
  and a half days of checks. So in the worst case about a fifth of a 30-day
  window is fetched from the table: 1,900 pages, not 8,143.
- **The index is not free.** Built fresh on this data it is 180 MB where the
  old one would be 140 MB, and every check written adds a wider entry. Writes
  were not part of this test.

## Worker count

The same test on the fixed code, changing only `WEB_CONCURRENCY`. 2 and 16 were
run once.

| Workers | Requests/s | Against 1 worker | p50 (ms) | p95 (ms) | `/monitors/` inside Django, p50 | `api` CPU | `postgres` CPU |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 235 / 222 | 1.0× | 210 / 220 | 270 / 290 | 10 ms | 71% | 40% |
| 2 | 422 | 1.8× | 110 | 150 | 11 ms | 141% | 80% |
| 4 | 703 / 754 | 3.2× | 67 / 63 | 100 / 86 | 12 ms | 281% | 156% |
| 8 | 1,119 / 1,085 | 4.8× | 42 / 43 | 62 / 65 | 15 ms | 522% | 270% |
| 16 | 1,124 | 4.9× | 34 | 120 | 20 ms | 587% | 289% |

Throughput does not grow in a straight line: eight workers give 4.8 times one
worker, not 8, and sixteen give no more than eight with a p95 twice as long.

- **The workers share what they wait for.** Each worker is its own process, but
  they all use one Postgres. The column "inside Django" is the same request
  doing the same three queries, and it takes longer the more workers there are:
  10 ms with one, 20 ms with sixteen. Each added worker makes the others a
  little slower, so the gain per worker shrinks.
- **The machine has 8 cores, and everything runs on it.** With 8 workers the
  API uses 5.2 cores and Postgres 2.7, and Locust (0.7 of a core) and Docker's
  port forwarding come on top: more than 8. The machine was 78% busy across its
  16 hardware threads. Workers 9 to 16 have no core to run on, so they take
  turns: the same requests a second, and a longer tail while they wait for CPU
  instead of waiting in gunicorn's queue.
- **Sync workers are busy for the whole request**, including the time they wait
  for Postgres to answer. More workers than cores still helps for that reason,
  up to the point where the cores themselves are full. gunicorn's own
  suggestion of `2 × cores + 1` assumes the machine runs nothing else.

On this machine 8 workers are worth having and 16 are not. On a server where
`api` has its own cores and Postgres is elsewhere the number is different, and
it would be found the same way: this test, twice per value.

## What I would look at next

In the order I would try them, one at a time, each with this same test.

1. **A database connection per request.** Django opens a new Postgres
   connection for every request and closes it afterwards (`CONN_MAX_AGE` is 0).
   Opening one took 4.9 ms when measured alone. The monitor list's three queries
   take 4.7 ms on a connection that is already open, and 12 ms in the test. At
   730 requests a second that is about 220 new Postgres processes a second, and
   it is probably why Postgres still uses 1.5 cores for queries that now take
   about a millisecond. `CONN_MAX_AGE` with `CONN_HEALTH_CHECKS` keeps one
   connection per worker. Expected: the two logged-in endpoints close to twice
   as fast inside Django. Not done here: one fix per test.
2. **The queue in front of the workers.** After the fix a status page request
   still takes 1 ms inside Django and 60 ms for the client. Threads in each
   worker (`gthread`) would let a worker take another request while one waits
   for Postgres, and a proxy in front could serve `/status` itself for a few
   seconds at a time.
3. **The status page when its cache is empty.** Its 90-day aggregate takes
   130 ms on this data. Every 15 seconds the cached page expires, and every
   worker that misses at that moment rebuilds it (a known limit in
   `ARCHITECTURE.md`). It is part of the p99 and the maximum above.
4. **Autovacuum on `checks`.** A lower `autovacuum_vacuum_insert_scale_factor`
   for this one table keeps the index-only scan from going back to the table
   for recent rows.
5. **The table itself.** The uptime query still reads every check in its
   window, and `checks` grows for ever. Hourly roll-ups and deleting old raw
   rows change both ([ADR 0022](docs/adr/0022-uptime-covering-index.md),
   option 3).

What this test does not say:

- Locust, Postgres and Pulse shared one machine, so they competed for the same
  cores. Locust itself used 67% of one core at 1,100 requests a second, under
  the 90% at which it warns that it is the limit.
- Nothing wrote to the database during a run. With `worker` running, checks are
  inserted while the reads happen.
- One size of data, 50 users, two minutes. It does not show what happens at ten
  times the checks, or after a day.
- Requests went through Docker's port forwarding on `localhost`, not a real
  network.
