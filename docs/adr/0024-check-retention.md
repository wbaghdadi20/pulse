# 24. Raw checks are kept a week; older ones survive as hourly sums with latency buckets

- Status: accepted, amended 2026-11-20 (ticket 1, subtask 1.2)
- Date: 2026-11-15 (D10 Extra)

## Context

Every check is a row in `checks`, and nothing ever deleted one. 500 monitors
checked every 5 minutes write 52 million rows a year, and ten times that at one
check every 30 seconds. The uptime endpoint and the status page read up to 90
days of them.

What is asked of old checks is narrow: how many there were, how many failed, and
the p95 of their latency, over 7, 30 or 90 days. A single old check is never
shown.

## Options

1. **Keep everything.** Exact answers. The table, its index and every backup
   grow for as long as Pulse runs.
2. **Delete rows older than 90 days and nothing else.** Bounded and still exact.
   The table stays thirteen times larger than a week of checks, and every 90-day
   read still walks every row.
3. **Hourly sums: count, failures and the hour's p95**, as the ticket describes.
   Counts add up exactly. A p95 does not: there is no way to get the p95 of a
   month from the p95 of each hour. Tried on the load-test data with the obvious
   stand-in, the average of the hourly p95 weighted by checks, it reported
   460 ms for a monitor whose p95 over the same 30 days of rows was 193 ms. An
   hour holds 12 checks there, so one timeout among them is that hour's "p95",
   and one hour in ten has one.
4. **Hourly sums with latencies counted in buckets** (chosen). Bucket counts of
   two hours add up to the bucket counts of both, and a percentile can be read
   from counts. Prometheus histograms and HDR histograms are this idea.
5. **Partition `checks` by day and drop old partitions.** Deleting becomes
   instant and leaves no dead rows behind. It composes with option 4 and is the
   next step at a larger size. Partitions have to be created ahead of time, and
   Django has no support for them.
6. **A time-series database or extension** (TimescaleDB's continuous
   aggregates). Built for exactly this. A different database image and a
   dependency on an extension, for one table.

For where the buckets live: a row per hour and bucket would be ten rows where
there is now one and would give most of the saving back. They are a JSON object
in the hour's row.

## Decision

**Buckets** (`monitors/latency.py`). Bucket `b` holds latencies from `1.1^b` to
`1.1^(b+1)` ms. Each bucket is 10% wider than the last, so the middle of a
bucket is within 5% of any latency in it, at 20 ms and at 20 s alike, in at most
109 buckets up to a 30 s timeout. The percentile is the middle of the bucket
that holds the latency of that rank.

**Rolling up** (`monitors/retention.py`). `check_hours` has one row per monitor
and clock hour (UTC): `checks`, `failures`, `latency_buckets`. Beat queues
`roll_up_checks` at ten past every hour. It sums up every hour that ended at
least five minutes ago and is not summed up yet, in one
`INSERT ... SELECT ... ON CONFLICT DO UPDATE`, so the rows never pass through
Python and running it twice, or twice at once, leaves the same rows. How far it
has got is not stored anywhere: it is the newest hour in `check_hours`.

**Deleting.** The same task then deletes raw checks older than
`PULSE_RAW_CHECK_DAYS` (default 7, cut at the top of an hour), but never one
whose hour is not summed up yet, and hourly rows older than 91 days, one day
more than the longest window anything reads.

**Reading** (`monitors/uptime.py`). A window of at most `PULSE_RAW_CHECK_DAYS`
is computed from raw checks as before and is exact. A longer one is the hourly
rows from the top of its first hour, plus the raw checks newer than the last
hour summed up. A check is in exactly one of the two.

## Consequences

Measured on a copy of the load-test database: 4,666,100 checks of 500 monitors.

- The first run summed them up into 388,800 hourly rows in 7.3 s and deleted
  3,657,100 raw checks in 1.5 s. Every later run handles one hour.
- Storage now depends on the number of monitors and how often they are checked,
  not on how long Pulse has run: a week of raw checks and 91 days of hours.
- Counts and uptime stay exact. p95 over 30 and 90 days is within 5% of the
  real one: 198 ms where the rows gave 193 ms. Over 7 days it is still exact.
- A 30- or 90-day window starts at the top of an hour, so it can include up to
  59 minutes more than asked for: 8,641 checks instead of 8,632 in the test.
- Reads did not get faster at this check interval. Uptime over 30 days went
  from one index-only query (2.2 ms) to three queries (4.4 ms), and the status
  page's 90 days from 62 ms to 83 ms, most of it Postgres unpacking the JSON
  buckets. What changed is that the cost no longer grows with the check
  interval or with time: a monitor checked every 30 s has the same 720 hourly
  rows a month as one checked every 5 minutes.
- An hourly row with its indexes is about 300 bytes, the size of two or three
  raw checks. The saving is 12 checks to one row at a 5-minute interval and 120
  to one at 30 seconds. At one check an hour there is none.
- Deleting does not shrink the `checks` file. Postgres reuses the space for new
  rows; giving it back to the system takes `VACUUM FULL` or option 5.
- There is no index on `checked_at` alone, so the hourly roll-up and delete
  each scan the raw table. That is a week of rows once an hour. A BRIN index
  would fit, since rows arrive in time order, if that scan ever matters.
- A single check older than a week cannot be looked at any more, only its
  hour's sums. A monitor not checked for a week shows no `latest_check`.
- Incident detection reads the last N raw checks of a monitor. With the bounds
  as they are (N at most 10) that needs `interval_s` under about 16 hours to
  stay inside a week of raw checks; a monitor checked once a day with N = 10
  would need a longer `PULSE_RAW_CHECK_DAYS`.
- A check saved more than five minutes after its hour ended is never summed
  up: it is counted for a week and then lost. A check's row is written when its
  probe finishes, and a probe is killed at 75 s.
- If the roll-up stops for more than a week nothing is lost: checks whose hour
  is not summed up are not deleted, and the next run catches up.
- The roll-up and the bucket sum are raw SQL, the only two statements in the
  app that are. Both are fixed text with parameters; the ORM cannot express
  `INSERT ... SELECT` or `jsonb_each_text`.

## Amended 2026-11-20 (ticket 1, subtask 1.2)

Checks and hours live in each organization's schema ([ADR 0026](0026-schema-per-tenant.md)).
`roll_up_checks` runs the roll-up, prune and forget in each organization in turn;
the raw SQL is unchanged and names no schema, so it acts on the one it runs in.
They are no longer the only raw SQL in the app: `orgs/services.py` adds the
template's and the advisory locks' statements, `CREATE SCHEMA` and the constraint
renames after a clone, and `monitors/management/commands/seed_checks.py` its
insert. Values travel as parameters and identifiers through
`psycopg.sql.Identifier`.
