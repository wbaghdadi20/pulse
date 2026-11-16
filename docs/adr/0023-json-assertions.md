# 23. A deep health check is judged by the keys asked for, not by its status code

- Status: accepted
- Date: 2026-11-09 (D5), recorded 2026-11-15

## Context

`shop` has two health endpoints. `/health` only proves the process answers.
`/ht/` (django-health-check) tests every dependency it knows of, here the
database, the cache and Redis, and answers 500 as soon as one of them fails.

Neither status code is the answer an operator wants. `/health` stays 200 while
`shop-redis` is stopped. `/ht/` goes 500 for any component, including ones
whose failure is not an outage for the people using the service, and the list
grows whenever the service's team adds a check. Whether a service is "down" is
a decision about which dependencies matter, and that decision belongs to the
monitor's owner, not to the status code.

## Options

1. **Trust the status code** (`expected_status: 200` on `/ht/`). Nothing to
   build. One unimportant component marks the whole service down, and the check
   cannot say which one failed.
2. **Match the body against a regular expression.** Works on any format. Easy to
   write one that matches too much, and matching user-supplied patterns against
   a body the target controls is a way to make a worker spin.
3. **A query language over the JSON** (JSONPath, JMESPath). Handles nested
   bodies and comparisons. A dependency and a language to validate, for bodies
   that are flat here.
4. **Named keys with the value each must have** (chosen).
5. **Ask the service for a narrower endpoint.** The cleanest result, and not
   available: the point of `shop` is a service someone else operates.

## Decision

A monitor has an optional `assertions` object, `{"<key>": "<expected value>"}`.
When it is set, `checks.probe` sends `Accept: application/json`, ignores the
status code, and the check passes only if the body is a JSON object in which
every listed key has exactly its expected value. Anything else, including a
body that is not JSON, is `assertion_failed`. Keys that are not listed are not
looked at. Without `assertions` a check is judged by `expected_status` as
before.

The serializer accepts a non-empty flat object of at most 20 string values.

## Consequences

- Stopping `shop-redis` fails the deep monitor while the shallow one stays
  green: the Checkpoint 1 demo. A component nobody listed can fail without
  opening an incident.
- The owner chooses what "down" means, per monitor, and can change it with a
  `PATCH`.
- The monitor is tied to the target's key names. django-health-check builds
  them from its configuration (`Redis(db=0, host='shop-redis', port=6379)`), so
  renaming a host there fails the monitor. It fails closed: a missing key is a
  failed check, never a silent pass.
- A check says that an assertion failed, not which one. The body is not stored.
- Flat keys and string equality only. A nested body or "at least 2 replicas"
  cannot be expressed; option 3 is the next step if a target needs it.
- A 500 with the asserted keys at `OK` is a passing check. That is the intent,
  and it is also how an outage the assertions did not anticipate goes unnoticed:
  a component that turns out to matter has to be added to the list by hand.
