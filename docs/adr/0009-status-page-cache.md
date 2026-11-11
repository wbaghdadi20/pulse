# 9. The status page is cached by key and dropped when an incident changes

- Status: accepted
- Date: 2026-11-11 (D7)

## Context

`GET /status` is public and shows, for every monitor, its state, 90-day uptime,
p95 latency and latest incidents. Building it takes four queries; one of them
aggregates every check of the last 90 days. On 500,000 checks that is about
0.4 s per request. The page is requested most when something is down, which is
also when the database is busiest.

## Options

1. **Make the query cheap instead** (daily roll-up rows, or a materialised
   view). Fixes the cause and helps the uptime endpoint too. A schema change and
   a new job; still one query per visitor.
2. **Django's `cache_page` on the view.** One line. The key is derived from the
   URL and request headers, so there is no simple way to delete "the status
   page" when an incident opens.
3. **Cache-aside under a key we choose** (chosen): look in the cache, on a miss
   build, store with a TTL, return.
4. **Build the page in a worker** on a timer and after every incident change;
   requests only read. No visitor ever waits for the queries and there is no
   stampede. More moving parts, and the page is rebuilt whether or not anyone
   reads it.
5. **HTTP caching** (`Cache-Control: max-age=15`, a CDN). Takes the load off
   Pulse entirely, but a response already handed to a browser or CDN cannot be
   taken back when an incident opens.

## Decision

`status_page.get_status_page` does cache-aside under the key `status:page`, with
a TTL from `PULSE_STATUS_CACHE_SECONDS` (default 15, 0 turns it off). What is
cached is the page as data, after the queries and before JSON rendering: the
queries are the cost.

`open_incident_for` and `resolve_incident` call `status_page.invalidate()`, which
deletes the key on commit. Before commit a concurrent request would rebuild the
page from the old rows and cache that for another full TTL.

The cache is Django's Redis backend on database 1. The broker stays on database
0, so clearing the cache cannot drop queued tasks.

## Consequences

Measured against 10 monitors, 500,000 checks and 60 incidents, gunicorn with two
workers, 2000 requests from 50 concurrent clients:

|               | p50      | p95       | throughput |
| ------------- | -------- | --------- | ---------- |
| without cache | 9,192 ms | 10,505 ms | 6 req/s    |
| with cache    | 50 ms    | 92 ms     | 718 req/s  |

- An incident opening or resolving shows on the next request. Everything else
  (uptime, p95, a monitor created, renamed or deleted) can be up to one TTL old.
- A request that read the old rows just before a commit can still store its page
  just after the delete. The page is then stale for at most one TTL.
- Stampede: when the key is missing, every concurrent request rebuilds the page.
  In the cached run above the slowest requests took 0.6 s for that reason, and
  an invalidation during an outage triggers it at the worst moment. Option 4, or
  a lock around the rebuild, would remove it. Not built.
- If Redis is unreachable `/status` answers 500: cache errors are not swallowed.
  Redis is already required as the broker; with a separate cache instance the
  page should fall back to building directly.
- The cache makes the page fast; it does not make the query fast. The first
  visitor after each expiry still waits 0.4 s, and that grows with `checks`.
  Option 1 is the fix for that and composes with this one.
- The page is identical for everyone and holds no monitor URLs, so one shared
  cached copy is safe.
