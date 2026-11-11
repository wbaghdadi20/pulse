# 2. Thin views and tasks over a service layer, the ORM as the repository

- Status: accepted
- Date: 2026-11-03 (D2), recorded 2026-11-10

## Context

The same business operations are reached two ways: over HTTP ("check now") and
from Celery (the scheduled check). D6 says so directly: reuse the check-now code,
do not copy it.

## Options

1. **`ModelViewSet` with logic in serializers and views.** The DRF default and
   the least code for CRUD. The check and incident logic would live in an HTTP
   class, where a Celery task cannot call it without faking a request.
2. **Fat models.** Django's traditional answer. `Monitor.check()` would mix the
   HTTP probe, incident rules and alert queueing into the persistence class.
3. **Views and tasks call plain functions in `services.py`** (chosen).
4. **Services plus a repository layer.** Common with SQLAlchemy. The Django ORM
   already is the abstraction over SQL; a wrapper around `Monitor.objects` would
   forward calls and hide `QuerySet` laziness the paginator relies on.

## Decision

- `views.py`: parse, call one service, shape the response. `APIView`, one method
  per verb, so each endpoint reads top to bottom.
- `tasks.py`: load the row by id, call the same service.
- `services.py`: the rules. No `Request`, no `Response`, no status codes. It
  raises domain errors (`core/errors.py`).
- `core/exception_handler.py`: the single place a domain error becomes 404 or 409.
- `incidents.py`, `checks.py`, `alerting.py`: one concern each, called by services.

## Consequences

- "Check now" and the scheduler cannot drift apart: one function.
- Services are tested without HTTP, views with DRF's `APIClient`.
- More files and more explicit code than a `ModelViewSet` for the plain CRUD.
- No repository to swap out: tests that touch data use the real Postgres test
  database, which is also what catches constraint behaviour.
- `PATCH` validates with a serializer that has no instance, then the service sets
  the fields. Validation that needs the old and new value together would have to
  move into the service.
