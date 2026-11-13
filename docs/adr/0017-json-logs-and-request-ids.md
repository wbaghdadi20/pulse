# 17. JSON logs on stdout, and one id that follows a request into the worker

- Status: accepted
- Date: 2026-11-12 (D9)

## Context

Pulse runs as three kinds of process (api, worker, beat) that nobody can attach
a debugger to. Before D9 each wrote text in its own format: gunicorn's, Celery's,
Django's. Nothing tied the lines of one request together, and a request that
opens an incident continues in a worker, in another container.

## Options for the format

1. **Text lines.** Readable by eye. Searching "errors for monitor 5" means a
   regular expression per format.
2. **One JSON object per line, on stdout** (chosen), with python-json-logger. It is
   a `logging.Formatter`: every `logging` call in Pulse, Django, Celery and
   gunicorn already goes through one.
3. **structlog.** Bound loggers and processors, and a second way of logging next
   to the standard one the libraries keep using.

## Options for getting the id onto every line

1. **Pass it to every function that logs.** Every signature changes, and library
   code never gets it.
2. **`threading.local`.** Right for gunicorn's sync workers, wrong under asyncio.
3. **A `ContextVar` and a logging filter** (chosen). Code logs as before; the
   filter reads the variable. Correct per thread and per asyncio task.
4. **django-guid.** The same design, packaged. About forty lines here, and the
   mechanism is the point of the exercise.

## Options for the caller's `X-Request-ID`

1. **Ignore it, always generate.** A gateway's id can no longer be followed in.
2. **Accept anything.** An untrusted string in every log line and a response header.
3. **Accept 1 to 64 characters of `[A-Za-z0-9._-]`, otherwise generate** (chosen).
   Not cleaned up: a cleaned id matches nothing the caller has.

## Decision

- `core.logs.logging_config(level)` builds one `dictConfig`: a stdout handler, the
  JSON formatter (`time`, `level`, `logger`, `message`, plus any `extra`), and
  `ContextFilter` on the handler. On the handler because a logger's filters are
  skipped for records arriving from child loggers.
- Each process needed its own way in:
  - Django reads it as `LOGGING`. The `django` logger's own handlers are removed.
  - Celery replaces the root handlers when a worker starts. A receiver on
    `setup_logging` applies the config and stops that. `--quiet` drops the banner,
    which is printed, not logged.
  - gunicorn's master never loads Django; it gets the dict as `logconfig_dict`.
- `RequestIDMiddleware` is first in `MIDDLEWARE`: it picks the id, sets the
  variable, puts the id in the response header and logs `request finished` with
  method, path, status and `duration_ms`.
- The variable is cleared on `request_finished`, not when the middleware returns:
  Django logs its own line for a 4xx or 5xx after the middleware chain.
- gunicorn's access log and runserver's request line are off. Both are written
  where the id is no longer set.
- In a task the filter adds `task_id` and `task` from Celery's current task. The
  request needs a variable of ours because Django keeps no current request;
  Celery already keeps a current task.
- A task queued during a request takes the id in its message headers
  (`before_task_publish`); the worker sets the variable for the length of the task
  (`task_prerun`, `task_postrun`).
- Not logged: query strings, request headers, request bodies. The `request` object
  Django attaches to its records is dropped (its text form has the query string),
  and `httpx` logs from WARNING up (at INFO it logs each checked URL in full).

## Consequences

- `docker compose logs api worker | grep <id>` shows a "check now" request and the
  alert delivery it caused.
- A task that beat queued has no request id. Its `task_id` is the handle.
- The id is for correlation, not identity: a caller that reuses one gets two
  requests under it.
- The worker's `Task ... received` line comes from its main process, outside any
  task, so the id is in `data.id` there and not in `task_id`.
- Still plain text: runserver's start-up banner (development only) and the
  Postgres and Redis containers.
- JSON is harder to read by eye. `docker compose logs --no-log-prefix api | jq` helps.
- `PULSE_LOG_LEVEL` applies to every logger. At DEBUG with `DJANGO_DEBUG=true`,
  Django logs SQL with its parameters, which are user data.
