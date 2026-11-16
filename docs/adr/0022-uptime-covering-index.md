# 22. The uptime aggregate is answered from a covering index

- Status: accepted
- Date: 2026-11-15 (D10)

## Context

The load test ([LOADTEST.md](../../LOADTEST.md)) put 50 users on Pulse with 4.7
million checks in the table. `GET /monitors/<id>/uptime/?days=30` was one request
in ten and took 58% of all the time the gunicorn workers were busy, so every
other endpoint queued behind it.

It is one query: `avg(ok)`, `percentile_cont(0.95)` over `latency_ms` and a
count, for one monitor over 30 days. `EXPLAIN (ANALYZE, BUFFERS)` showed the
index on `(monitor_id, checked_at)` finding the 8,639 entries in 3 ms on 61
pages, and then 8,143 table pages read to fetch the two columns the index did
not hold. A monitor's checks are written minutes apart with every other
monitor's checks in between, so nearly every row is on a page of its own:
64 MB read, with `shared_buffers` at 128 MB, for three numbers.

## Options

1. **Carry `ok` and `latency_ms` in the index** (`INCLUDE`), so the query is an
   index-only scan (chosen). One migration, no change in behaviour, and the
   answer stays exact and current.
2. **Cache the response per monitor**, as the status page is. The first request
   after each expiry still pays the 50 ms, the load test spreads its requests
   over 500 monitors so most would miss, and uptime would be allowed to be
   stale. It hides the query instead of fixing it.
3. **Hourly roll-up rows**, read instead of raw checks: 720 rows for 30 days
   instead of 8,640. It also stops the table growing without limit. A schema
   change, a scheduled job, and a p95 that can no longer be exact, because
   percentiles of separate hours do not combine. The right next step, and a
   bigger one than a load test's single fix.
4. **Store each monitor's rows together**: `CLUSTER` on the index, or partition
   by monitor. `CLUSTER` rewrites the table under an exclusive lock and is
   undone by the next day of inserts. A partition per monitor means creating
   tables whenever the API creates a monitor, and hash partitions only spread
   the same scatter over more tables.
5. **Give Postgres more memory.** The 64 MB per request would be read from
   memory instead of the page cache. Cheaper per request, still 8,143 pages
   per request, and the table keeps growing.

## Decision

`checks_monitor_checked_cover` on `(monitor_id, checked_at) INCLUDE (ok,
latency_ms)` replaces `checks_monitor_checked_idx`. It has the same key
columns, so everything that used the old index uses the new one, and there is
still one index on the table.

`uptime.aggregates()` counts `*` instead of `id`. `id` is never null, so the
number is the same, but `id` is not in the index and asking for it sent
Postgres back to the table for every row.

Migration `0011` builds the new index with `CREATE INDEX CONCURRENTLY` and
drops the old one afterwards, so `checks` is never without an index and the
workers' inserts are not blocked while it builds. That needs a migration that
is not atomic.

## Consequences

- The query went from 8,216 pages and about 50 ms to 61 pages and 1.5 ms. In
  the same load test, throughput went from about 475 to about 730 requests a
  second and the endpoint's p95 from about 185 ms to about 100 ms.
- The status page's 90-day aggregate reads the same four columns and is an
  index-only scan too when its cache is empty.
- The index is larger: 180 MB against 140 MB for the old shape, both built
  fresh on the test data, and every check written adds a wider entry.
- An index-only scan skips the table only for pages `VACUUM` has marked as
  visible to everyone. Rows written since the last vacuum are still fetched
  from the table: with one unvacuumed day, 290 of them and 343 pages instead of
  50. By default autovacuum visits an insert-only table once the new rows reach
  20% of it, so up to about a fifth of a 30-day window can be in that state. A
  lower `autovacuum_vacuum_insert_scale_factor` on `checks` would shrink that.
  Not changed here.
- A test asks Postgres for the plan and fails if it is not an index-only scan:
  an aggregate added over a column the index does not carry would otherwise
  bring the table reads back with no test noticing.
- A `CONCURRENTLY` build that fails part-way leaves an invalid index behind,
  which has to be dropped by hand before the migration is run again.
- The query still reads every check in the window, 8,639 index entries for 30
  days at one check per 5 minutes and ten times that at one per 30 seconds.
  Option 3 is what changes that.
