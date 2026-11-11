# 12. A monitor's latest check comes from the same query as the monitor

- Status: accepted
- Date: 2026-11-11 (D3 Extra)

## Context

The monitor representation gained `latest_check`. The first version asked for
it in the serializer, once per monitor: `2 + N` queries for a page of `N`
monitors. That is the N+1 problem: the code looks like one line, and the cost
hides in a loop the framework runs.

## Options

Measured on 10 monitors with 50,000 checks each, listing all ten:

| Approach                                              | Queries | Time    |
| ----------------------------------------------------- | ------- | ------- |
| one query per monitor (N+1)                           | 11      | 10 ms   |
| correlated subquery with `LIMIT 1` (chosen)           | 1       | 2 ms    |
| `prefetch_related` with a queryset sliced to `[:1]`   | 2       | 435 ms  |

1. **N+1.** Simple. The query count grows with the page, and each query is a
   round trip.
2. **`prefetch_related` with a sliced `Prefetch`.** The usual Django answer to
   N+1, and a constant two queries. To take one row per monitor Django ranks
   every check of those monitors with `ROW_NUMBER()`, so it reads all 500,000
   rows to keep ten. Fewer queries, far more work.
3. **A correlated subquery per monitor, returning the check as a JSON object**
   (chosen). One statement. For each monitor Postgres walks the
   `(monitor_id, checked_at)` index backwards and stops at the first entry.
4. **One scalar subquery per column.** Keeps native types, but probes the index
   five times per monitor instead of once.
5. **A `latest_check` foreign key on `monitors`**, updated by every check. The
   cheapest read. A second copy of a fact that `checks` already holds, to keep
   right under concurrent checks.

## Decision

`services._monitors()` annotates `latest_check` with
`Subquery(... .values(JSONObject(...))[:1])`. `list_monitors` and `get_monitor`
both go through it, so a monitor has the same shape wherever it is returned.

## Consequences

- A page is two queries with limit/offset (count, page) and one with `?after=`,
  whatever the page size. Tests pin both numbers.
- The cost is one index probe per monitor on the page, at most 100.
- JSON has no timestamp type, so `checked_at` comes back as text. The serializer
  parses it so it is rendered like every other timestamp.
- A monitor returned straight from `create_monitor` has no annotation. It has no
  checks either, and the field's `allow_null` renders `null`.
- Fewer queries is not the goal by itself: option 2 has fewer than option 1 and
  is forty times slower here. The plan is what was checked, not the count.
