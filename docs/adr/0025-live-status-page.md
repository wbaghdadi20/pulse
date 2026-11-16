# 25. The status page updates by polling, and `/status` answers 304 when nothing changed

- Status: accepted
- Date: 2026-11-15 (D10 Extra)

## Context

`/status` was JSON that someone had to ask for again to see a change. A status
page is looked at during an outage, by people waiting for it to turn green.

`api` runs gunicorn with sync workers: a worker serves one request from start to
finish and nothing else meanwhile. The load test ran with 4 of them.

## Options

1. **Reload the whole page on a timer** (`<meta http-equiv="refresh">`). No
   script. Every viewer downloads and redraws everything every few seconds,
   changed or not.
2. **Poll the JSON, with a conditional request** (chosen). The page asks
   `/status` every 5 seconds and sends the ETag of the copy it has. Unchanged:
   `304 Not Modified`, no body, nothing redrawn.
3. **Server-Sent Events.** The server pushes a change the moment it happens,
   over one connection per viewer that stays open. With sync workers every open
   tab would hold a worker for as long as it is open: four viewers and the API
   stops answering. It needs an ASGI server (uvicorn or daphne), async views,
   and something to wake each connection when an incident changes (Redis
   pub/sub). A different way of running the whole API, for one page.
4. **WebSockets** (Django Channels). The same cost as option 3 and a channel
   back from the browser that nothing would use.
5. **Long polling.** The request is held until something changes. It holds a
   worker exactly as option 3 does.

## Decision

`GET /status` carries an `ETag` and `Cache-Control: no-cache`. The tag is a hash
of the page's monitors and is kept in the cache next to the page, so a request
that hits the cache computes nothing. `generated_at` is left out of the hash: it
changes every time the page is rebuilt, and a rebuilt page that says the same is
not a change. Two such pages are equivalent but not identical bytes, which is
what a weak tag (`W/"..."`) means. Matching `If-None-Match` is left to Django's
`get_conditional_response`, which knows lists of tags, weak tags and `*`.

`GET /status/live` is a small HTML page with no data in it. Its script asks
`/status` every 5 seconds, redraws on a 200 and does nothing on a 304. It stops
asking while the tab is hidden. Names are written with `textContent`, and the
response carries a Content-Security-Policy that allows only the page's own
script and style (a nonce, new for every response) and requests to Pulse itself.

## Consequences

- An incident shows within 5 seconds with nobody touching anything: opening or
  resolving one already drops the cached page (ADR 0009), and the next poll
  gets the new one.
- A viewer costs one request every 5 seconds, each of them one Redis read: in
  the load test `/status` took 1 ms inside Django. `/status` is not rate
  limited, as before.
- How often the answer is a 304 depends on the monitors. The cached page lives
  15 seconds, so two polls in three are a 304 at least. After a rebuild it is a
  304 only if no number moved, and `uptime_percent` has two decimals: a monitor
  that is not at 100% moves a little with nearly every check.
- The saving per 304 is small today, about 500 bytes for three monitors, and
  grows with the number of public monitors.
- Up to 5 seconds late, where option 3 would be immediate. If that ever
  matters, the price is the move to ASGI described there.
- The page needs JavaScript. Without it a line points to `/status`.
- `/status` stays what it was for every other client: the same JSON, two more
  headers.
