# Security

Six attacks on Pulse, each one tried against the running stack before it was fixed.
The outputs are copied from those runs. Decisions and their alternatives are in
ADRs [0013](docs/adr/0013-authentication.md) to [0016](docs/adr/0016-rate-limiting.md).

| # | Attack | Fixed by |
| - | ------ | -------- |
| 1 | [Using the API without logging in](#1-using-the-api-without-logging-in) | a token on every endpoint |
| 2 | [Reading and changing someone else's monitor (IDOR)](#2-reading-and-changing-someone-elses-monitor-idor) | every lookup filtered by owner |
| 3 | [Making Pulse call internal addresses (SSRF)](#3-making-pulse-call-internal-addresses-ssrf) | resolve, judge the address, connect to it |
| 4 | [Guessing passwords](#4-guessing-passwords) | 5 attempts a minute per address |
| 5 | [Setting the owner from the body (mass assignment)](#5-setting-the-owner-from-the-body-mass-assignment) | nothing to fix; pinned by a test |
| 6 | [SQL injection](#6-sql-injection) | bound parameters |

## 1. Using the API without logging in

**Attack.** Anyone who can reach port 8100 uses the API as if it were theirs.

**What I tried.** `POST /monitors/` and `POST /monitors/<id>/check/` with no
credentials at all.

**What happened before.** Both worked. Every monitor in attack 3 was created this way.

**The fix.**

- `POST /signup` and `POST /login`. Passwords are hashed with argon2id by Django's
  hasher; the plain password is never stored, logged or returned.
- Login returns a JWT (HS256, signed with `PULSE_JWT_SECRET`, valid for
  `PULSE_JWT_TTL_SECONDS`). `accounts.authentication.JWTAuthentication` reads it
  from `Authorization: Bearer <token>`.
- `IsAuthenticated` is the default for every view. A view is public only by
  subclassing `core.views.PublicAPIView`: `/health`, `/status`, `/signup`, `/login`.
- A missing, malformed, expired or forged token, or one whose account was deleted,
  answers 401 with `WWW-Authenticate: Bearer`.
- An unknown email and a wrong password get the same 401 and the same body, and
  take the same time: Django hashes a password even when the email is unknown.

**The test.** `tests/accounts/test_authentication.py` walks the URL configuration:
every route answers 401 without a token for every method, and the routes that do
not are exactly the four above. A new route is protected unless that list changes.
`tests/accounts/test_tokens.py` covers expiry, a wrong secret, an edited payload,
`alg: none`, another algorithm and missing claims.

## 2. Reading and changing someone else's monitor (IDOR)

**Attack.** Monitor ids are sequential. A logged-in user changes the id in the URL.

**What I tried.** Two accounts, alice and bob. Alice created monitor 1. Bob, with
his own valid token, listed monitors, then read, renamed, checked and deleted
monitor 1.

**What happened before.** Everything worked:

```
# bob lists monitors
1 ['alice-payments']
# bob reads monitor 1
{"id":1,"name":"alice-payments","url":"http://flaky:8000/health", ...  -> 200
# bob renames monitor 1
PATCH -> 200
# bob runs a check on monitor 1
POST check -> 200
# bob deletes monitor 1
DELETE -> 204
# alice looks for her monitor
{"detail":"Monitor 1 not found."} -> 404
```

**The fix.** `services.get_monitor(pk, owner)` and `services.list_monitors(owner)`
filter by owner in the query itself (`WHERE id = %s AND owner_id = %s`), and every
view reaches a monitor through them. The owner is a required argument, so a call
that leaves it out does not type-check. Someone else's monitor answers 404 with
the same body as an id that was never used; 403 would confirm the id exists.
Monitor names are unique per owner, not across everyone: a 409 for a name another
user has would leak it.

After:

```
# bob lists monitors
1 ['planted']                                   <- his own only
{"detail":"Monitor 3 not found."} <- bob GET /monitors/3/ -> 404
{"detail":"Monitor 3 not found."} <- bob PATCH /monitors/3/ -> 404
{"detail":"Monitor 3 not found."} <- bob POST /monitors/3/check/ -> 404
{"detail":"Monitor 3 not found."} <- bob GET /monitors/3/uptime/ -> 404
{"detail":"Monitor 3 not found."} <- bob POST /monitors/3/incidents/ -> 404
{"detail":"Monitor 3 not found."} <- bob DELETE /monitors/3/ -> 404
{"detail":"Monitor 99999 not found."} <- bob GET /monitors/99999/ -> 404
alice GET -> 200
```

**The test.** `tests/monitors/test_ownership.py`: user B gets 404 on all six ways
to reach a monitor by id (read, edit, delete, check, uptime, open an incident),
the body matches that of an unused id, and nothing changed afterwards. One test
compares that list with the URL configuration, so a new `/monitors/<id>/...` route
fails until it is covered. With the owner filter removed, 15 of the 19 tests fail.

The public status page is the deliberate exception: it shows monitors of every
owner, but only those with `is_public` set (default off), and nothing about the
others is read. `tests/monitors/test_status_page.py`.

## 3. Making Pulse call internal addresses (SSRF)

**Attack.** Pulse's job is to request URLs users give it, and it does so from
inside the Docker network. A user points a monitor at something only Pulse can reach.

**What I tried.** Monitors for five addresses, then "check now" on each.

**What happened before.** All five were created. The results:

| URL | Result | What it reveals |
| --- | ------ | --------------- |
| `http://postgres:5432/` | `error` in 8 ms | the name exists, the port is open, and it does not speak HTTP |
| `http://redis:6379/` | `error` in 2 ms | the same |
| `http://localhost:9999/` | `connection_refused` in 1 ms | the port is closed |
| `http://localhost:8000/health` | `ok`, 200 | Pulse can call itself; any admin-only service next to it would answer too |
| `http://169.254.169.254/` | `connection_refused` in 3 ms | nothing listens there on a laptop. On AWS or GCP this address returns the server's credentials |

Open (`error`) and closed (`connection_refused`) ports can be told apart without
reading any response, so Pulse was a port scanner for its own network.

**The fix.** `monitors/ssrf.py`, used in two places:

- **On create and edit:** the serializer answers 400.
- **On every check:** `probe` records the result `blocked` and sends nothing. DNS
  can change after a monitor is created.

How it decides:

1. The host is resolved with `socket.getaddrinfo`. Spellings such as `127.1`,
   `0x7f.0.0.1` and `2130706433` all come out as `127.0.0.1`; the URL text is
   never inspected.
2. Every address is judged. It is forbidden if it is private, loopback,
   link-local, reserved or multicast (the ticket's five), or not globally routable.
3. If any address is forbidden, the host is refused, unless its name is in
   `PULSE_ALLOWED_PRIVATE_HOSTS` (`flaky,shop`). That list comes from the
   environment; nothing in the API can add to it.
4. The check connects to the address that was judged, not to the name, so a
   second DNS answer cannot differ from the first (DNS rebinding). The name still
   goes in the `Host` header and the TLS handshake.
5. Redirects are not followed.

The gap in the five properties, measured on Python 3.14:

| Address | Any of the five? | `is_global` | Without the extra rule |
| ------- | ---------------- | ----------- | ---------------------- |
| `100.100.100.200` (a cloud metadata service, in `100.64.0.0/10`) | no | no | allowed by the five alone |
| `224.0.0.1` (multicast) | yes | yes | allowed by `is_global` alone |
| `64:ff9b::7f00:1` (NAT64 form of `127.0.0.1`) | yes | yes | allowed by `is_global` alone |

After:

```
{"url":["This host is on a private or internal address, which Pulse does not check."]} <- http://postgres:5432/ -> 400
... the same 400 for redis:6379, 169.254.169.254, localhost:8000, localhost:9999, 127.1, 2130706433, [::1]:8000
http://flaky:8000/   -> {"ok":true,"status_code":200,"latency_ms":2,"error_type":null}
https://example.com/ -> {"ok":true,"status_code":200,"latency_ms":79,"error_type":null}
```

**The test.** `tests/monitors/test_ssrf.py`: monitors for `postgres:5432`,
`redis:6379`, `169.254.169.254` and `127.0.0.1` are rejected, the allow-listed
targets are created and checked, a host that turns internal after creation is not
called, the connection goes to the judged address after exactly one lookup, and a
302 to `127.0.0.1` is not followed. Every other test runs with a fake resolver, so
none depends on real DNS.

## 4. Guessing passwords

**Attack.** `/login` answers as fast as it can, as often as asked.

**What I tried.** Twenty wrong passwords for one account, in a loop.

**What happened before.**

```
401 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401
elapsed: .914875587 s
```

About 1,300 guesses a minute from one laptop, limited only by the cost of argon2.

**The fix.** A fixed one-minute window per client address, counted with an atomic
increment in Redis (`core/ratelimit.py`), applied by DRF throttles
(`core/throttling.py`):

- `/login` and `/signup`: `PULSE_AUTH_RATE_LIMIT_PER_MINUTE`, default 5. Signup is
  included because each one costs a password hash.
- The rest of the API: `PULSE_RATE_LIMIT_PER_MINUTE`, default 60.
- Over the limit: 429 with `Retry-After`, the seconds left in the minute. A blocked
  login never reaches the password check.
- `X-Forwarded-For` is ignored unless `PULSE_NUM_PROXIES` says a proxy is in front.
  DRF's default believes the header, which lets a client claim a new address on
  every request.
- `/health` and `/status` are not limited.

After:

```
401 401 401 401 401 429 429 429
HTTP/1.1 429 Too Many Requests          <- sent with a made-up X-Forwarded-For
Retry-After: 31
{"detail":"Request was throttled. Expected available in 31 seconds."}
:1:rl:auth:172.23.0.1:29907859 = 9, ttl 60    <- the counter in Redis
health during the block: 200
```

**The test.** `tests/core/test_rate_limit.py`: the sixth login in a minute is a 429
with `Retry-After`, a forged `X-Forwarded-For` does not reset the count, and the
window arithmetic is tested with the clock passed in.

## 5. Setting the owner from the body (mass assignment)

**Attack.** Send a field the server should decide, hoping it is copied onto the row.

**What I tried.** As bob: `POST /monitors/` with `"owner": 1, "owner_id": 1`
(alice's id).

**What happened before.** Nothing: the monitor was created for bob.

```
1|owned-by-bob|1
2|planted|2          <- owner_id 2 is bob, whatever the body said
```

**The fix.** None was needed. A DRF serializer only reads the fields it lists as
writable, and `owner` is not a field of `MonitorSerializer` at all: it is set from
the token in the view. Unknown fields are dropped, not rejected, so a client can
send back an object it read.

**The test.** `tests/monitors/test_mass_assignment.py` fails if `owner`, `id`,
`created_at`, `last_checked_at` or `next_check_at` ever becomes writable, on
create or on edit, and checks the same for `/signup`.

## 6. SQL injection

**Attack.** Input that ends a string in a SQL statement and continues as SQL.

**What I tried.** Pulse has no such query, so I wrote one by hand:
`f"... WHERE owner_id = {owner_id} AND name = '{name}'"`, and gave it
`' OR '1'='1` and `' UNION SELECT password_hash FROM users --`.

**What happened before.** The first returned every owner's monitors: the `OR` is
true for every row, so the owner filter stops mattering. The second returned a
password hash from another table through a search by name. A name with an
ordinary apostrophe was a syntax error.

**The fix.** Bound parameters: `cursor.execute("... WHERE owner_id = %s AND name =
%s", [owner_id, name])`. The statement and the values travel separately and the
database never parses a value. The Django ORM does this for every query, and so
does Pulse's one raw statement (`seed_checks`).

**The test.** `tests/monitors/test_sql_injection.py` holds both versions and runs
the same inputs through each. Through the API, the attack string is stored and
returned as a plain monitor name.

## Secrets

`.env` has never been committed. `git log -p --all | grep -iE "password|secret|token"`
matches 335 lines, all of them names, placeholders (`change-me`) or test values.
One real-looking value is in the history: the `django-insecure-...` key that
`startproject` generates, committed and replaced by `DJANGO_SECRET_KEY` on the
first day. It is not in use.

## What is still open

| Limit | Effect |
| ----- | ------ |
| A token cannot be revoked | a stolen token works until it expires (one hour by default). Deleting the account does stop it |
| Signup answers 409 for a registered email | it reveals who has an account. Hiding that needs email verification |
| Redirects are not followed | a monitor of a URL that redirects records the 3xx. Following them safely means judging every hop; see [ADR 0015](docs/adr/0015-ssrf-guard.md) |
| Creating a monitor for an internal name answers 400, an unknown name 201 | the difference tells a user which internal names resolve |
| An allow-listed hostname is trusted whatever it resolves to | whoever controls DNS for `flaky` or `shop` inside the network controls where those checks go |
| The fixed window resets on the minute | 5 attempts at :59 and 5 at :00 are all allowed: twice the limit across a boundary |
| Limits are per address | users behind one NAT share a count; an attacker with many addresses gets many counts |
| No lockout per account | see below |
| The rate limiter needs Redis | with Redis down the API answers 500 rather than run unlimited |

## Account lockout

Locking an account after too many failed logins stops guessing one account's
password from many addresses, which a limit per address does not. The cost is a
new attack: anyone who knows your email can lock you out by failing on purpose,
as often as they like. Pulse limits per address only. Slowing down per account
instead of locking it (a growing delay, or a second factor) avoids handing that
switch to an attacker.
