# 15. Checks resolve the host themselves and connect only to an address they judged

- Status: accepted
- Date: 2026-11-12 (D8)

## Context

Pulse requests URLs that users give it, from inside its own network. Before D8 a
monitor for `http://postgres:5432/` was accepted, and "check now" told the user
whether the port was open (SECURITY.md, attack 3). On a cloud server the same
trick reaches the metadata address that hands out the server's credentials.

## Options for the rule

1. **Look at the URL text** (reject "localhost", "127.", "10."). `127.1`,
   `0x7f.0.0.1`, `2130706433`, `[::1]` and any public name with an A record of
   `127.0.0.1` all pass.
2. **Resolve the host and test the address** with `is_private`, `is_loopback`,
   `is_link_local`, `is_reserved`, `is_multicast`. Misses `100.64.0.0/10`, where
   `100.100.100.200` is one cloud's metadata service: none of the five is set.
3. **The five, or not `is_global`** (chosen). `is_global` alone would let through
   multicast and the NAT64 form of `127.0.0.1`, which count as global.

## Options for when

1. **When the monitor is created.** DNS can point elsewhere a minute later.
2. **Also before every check.** Still two lookups per check: the one that is
   judged, and the one httpx does to connect. An attacker's DNS server can answer
   them differently (DNS rebinding).
3. **Also connect to the judged address** (chosen): one lookup, and the connection
   goes to its answer.

## Options for redirects

1. **Follow them, judging every hop.** httpx's own `follow_redirects` cannot be
   used, since it resolves and connects by itself. `probe` would need its own
   loop: judge, request, read `Location` (relative, missing, another scheme),
   repeat up to a limit, all under the one `timeout_s` deadline, with a new
   `too_many_redirects` result and a per-monitor switch so a 301 can still be what
   someone monitors. About fifty lines, each of them security-relevant.
2. **Do not follow** (chosen). A 3xx is the check's result.

## Options for our own targets

`flaky` and `shop` are on private addresses, so the rule blocks them.

1. **Allow address ranges** (`172.16.0.0/12`). Also allows `postgres` and `redis`.
2. **Allow hostnames from configuration** (chosen): `PULSE_ALLOWED_PRIVATE_HOSTS`.
3. **Send checks through an egress proxy or a network policy** that only permits
   chosen destinations. The right second layer in production. Not built.

## Decision

- `monitors/ssrf.py`: `safe_address(url)` parses the URL with httpx (the parser
  that will send the request), resolves the host with `socket.getaddrinfo`, and
  raises `BlockedAddress` if any of its addresses is forbidden and the host is not
  allow-listed. It returns the first address.
- `MonitorSerializer.validate_url` calls it on create and edit: 400. A host that
  does not resolve is accepted, as before.
- `checks.probe` calls it on every check. Blocked is a result, `blocked`, recorded
  like any failure; nothing is sent.
- `probe` requests the returned address, with the original name in the `Host`
  header and in the `sni_hostname` extension, so virtual hosts and certificate
  verification behave as if the name had been used.
- The probe client is built with `follow_redirects=False` and `trust_env=False`:
  a proxy variable in the environment would otherwise decide where it connects.
- The allow-list matches whole hostnames, case-insensitively, and is read from the
  environment only.
- `PULSE_WEBHOOK_URL` is not guarded. It is private too, but it comes from
  configuration, not from a user.

## Consequences

- **Redirects:** a monitor of a URL that redirects records the 3xx and is "down"
  unless `expected_status` says otherwise. The owner points it at the final URL.
  Option 1 above is what adding them would take; `safe_address` would be reused
  unchanged, once per hop.
- An allow-listed name is trusted whatever it resolves to. Whoever controls DNS
  for `flaky` inside the network controls where those checks go.
- Since D9 `api`, Pulse itself, is on the list, so that a monitor can watch
  `http://api:8000/ready` (ADR 0018). The list is by host, not by path: any user
  can point a monitor at any path of Pulse's API from inside the network. Such a
  check carries no token, so it gets what an anonymous caller gets from outside,
  and its owner sees a status code, a latency and whether their JSON assertions
  held, never the body itself. Each one costs Pulse a request to itself, bounded
  by the smallest `interval_s`, 10 s.
- `api` also has to be in `DJANGO_ALLOWED_HOSTS`: the check keeps the name in the
  `Host` header, and Django answers 400 to a host it was not told to serve.
- Creating a monitor answers 400 for an internal name and 201 for an unknown one.
  That difference tells a user which internal names exist. Accepted: the
  alternative is to refuse every name that does not resolve yet.
- Only the first address is used. A host with one dead and one live address fails
  where a client that tried both would succeed.
- `getaddrinfo` has no timeout. A slow resolver holds an API worker for as long as
  the system resolver waits; the rate limit bounds how often.
- Scanning *public* addresses through Pulse is still possible. That is what an
  uptime monitor does.
- A monitor that starts resolving to an internal address fails its checks with
  `blocked`, opens an incident and alerts its owner like any outage.
- `tests/conftest.py` replaces the resolver for every test, so the suite gives the
  same answers in Docker, where `postgres` resolves, and outside it.
