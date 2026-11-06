# Debugging notes

The method, every time: reproduce it, write down a hypothesis, prove or disprove
exactly that, fix it, add the test that would have caught it.

## Three bugs from this week

### 1. Docker service names were rejected as monitor URLs (D2)

**What I saw.** `POST /monitors/` with `"url": "http://django:8000/ht/"` answered
400 `Enter a valid URL.` The same URL worked from `curl` inside the container.

**What I suspected.** The field used Django's `URLValidator`, and I suspected it
requires a dot in the hostname (a TLD), which Docker service names don't have.

**How I proved it.** In `manage.py shell`, `URLValidator()("http://django:8000/")`
raised `ValidationError` while `URLValidator()("http://django.local:8000/")` passed.
Django's host regex only exempts `localhost`. It also accepts `ftp://`, which a
monitor can't check.

**How I fixed it.** `monitors/validators.py::validate_http_url` uses `urlsplit` and
checks the three things Pulse actually needs: an `http`/`https` scheme, a hostname,
a numeric port. Test: `test_accepts_http_urls_including_docker_hostnames`, which
pins `http://django:8000/ht/` so this cannot regress silently.

### 2. The second open incident answered 500 instead of 409 (D3)

**What I saw.** `POST /monitors/<id>/incidents/` twice: the second call returned a
500 with an `IntegrityError` for `uq_one_open_incident`. In the test suite the
failure also poisoned the rest of the test: Postgres answered every later query
with `current transaction is aborted`.

**What I suspected.** Two separate problems. The partial unique index was doing
its job, but nothing turned the database's "no" into an HTTP answer. And the
failed `INSERT` left the test's wrapping transaction in an aborted state.

**How I proved it.** Two `psql` sessions reproduced the race by hand: B's insert
waited for A's commit, then failed on the constraint (transcript in the README).
For the second problem, printing `connection.needs_rollback` after the failed
insert showed `True`.

**How I fixed it.** `services.open_incident_for` catches `IntegrityError`, reads
the constraint name from psycopg's `diag` (`core/db.py::violated_constraint`) and
raises `IncidentAlreadyOpen`, which `core/exception_handler.py` maps to 409. The
insert runs inside `transaction.atomic()`, which is a savepoint when a transaction
is already open, so the failure is rolled back to the savepoint instead of
aborting the whole transaction. Test: `test_second_open_incident_is_409_never_500`.
20 concurrent requests gave one 201 and nineteen 409s.

### 3. mutmut was testing the original code, not the mutants (D5)

**What I saw.** `mutmut run` generated 27 mutants for `monitors/incidents.py`, ran
the 62 tests (all green), then stopped: "could not find any test case for any
mutant". The same tests kill every mutant once the setup is right, so something
was wrong with the setup, not the tests.

**What I suspected.** mutmut copies the code into `mutants/` and runs pytest from
there. The tests passed, so they imported *a* `monitors.incidents`. My hypothesis:
they imported the original one from the project root, where nothing is mutated.

**How I proved it.** A throwaway test printed `monitors.incidents.__file__`,
`hasattr(module, "x_decide__mutmut_orig")` and `sys.path[:4]` when run from
`mutants/`. The file was the original, the trampoline attribute was missing, and
`sys.path[1]` was the project root. pytest-django puts the directory holding
`manage.py` on `sys.path` before anything else, so Django imported the real
package first and the copy was never used.

**How I fixed it.** In `pyproject.toml`: `pythonpath = ["."]` so imports resolve
from the rootdir, and `django_find_project = false` so pytest-django stops
searching for `manage.py`. mutmut's own "forced fail" stage then confirmed the
mutants were live, and the first real run killed 27/27.

## Finding a commit with git bisect

Setup: a throwaway branch with ten small commits, one of which changed
`len(results) < n` to `len(results) <= n` in `_last_n_all`, so a streak of exactly
N failures no longer opened an incident. The oracle is the pure incident tests:
`pytest -q -x tests/monitors/test_incidents.py` exits 0 when they pass.

```
$ git log --oneline 2b0a5a3..HEAD
a980a5e docs: keep incidents.py framework-free
d09e738 docs: pointer from errors.py to the exception handler
004d3f7 docs: pointer to pagination settings
b38d65c docs: restate the thin-view rule
c52acc3 refactor: tidy the length guard in _last_n_all
6018141 docs: contrast percentile_cont with percentile_disc
b4f21d6 docs: say where validate_http_url is used
ed363e0 docs: point checks.py at the error_type choices
39673a7 docs: docstring for list_monitors
9c0e7bb docs: note result ordering in incidents.py

$ git bisect start
status: waiting for both good and bad commits
$ git bisect bad                  # HEAD: tests fail
status: waiting for good commit(s), bad commit known
$ git bisect good 2b0a5a3          # last commit known to pass
Bisecting: 4 revisions left to test after this (roughly 2 steps)
[6018141c4d28c2fbc272b7379b94326753dc4203] docs: contrast percentile_cont with percentile_disc
$ git bisect run pytest -q -x tests/monitors/test_incidents.py
Bisecting: 2 revisions left to test after this (roughly 1 step)
[b38d65cb291d2fec2910bff00ce2421c33750db3] docs: restate the thin-view rule
F
Bisecting: 0 revisions left to test after this (roughly 0 steps)
[c52acc3d8855077200a40d6f2636442a700b522c] refactor: tidy the length guard in _last_n_all
F
c52acc3d8855077200a40d6f2636442a700b522c is the first bad commit
 monitors/incidents.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
bisect found first bad commit

$ git bisect reset
```

Ten commits, two test runs: bisect halves the range each step, so it needs about
log2(n) runs. Two things bit me on the way. The run command must exit 0 for good
and 1-127 for bad; the first attempt used a script that wasn't executable, the
shell returned 126 and git reported "bogus exit code" and refused to continue.
And the oracle has to work on *every* commit in the range, so it must not depend
on files that only exist at the tip.

## Stepping through with a debugger

`breakpoint()` on the first line of `decide()` and
`docker compose exec api pytest -s tests/monitors/test_incidents.py::test_three_failures_in_a_row_opens`
stops in pdb inside the container (`-s` so pytest doesn't swallow the prompt).
`p results, incident_open` shows the inputs, `n` steps to the next line, `s` steps
into `_last_n_all`, `c` continues. `pytest --pdb` instead drops into the debugger
only when a test fails, which is the one I reach for.

## Shallow vs deep health checks

A **shallow** check says "the process is up and answering": `GET /health` returns
`{"status": "ok"}` without touching anything. A **deep** check says "and the things
I depend on work too": `shop`'s `/ht/` runs a `SELECT 1` on Postgres, a set/get on
the cache and a `PING` on Redis, and answers 500 if any of them fails.

Both are right for different readers. A load balancer or Kubernetes decides
whether to send traffic or restart the container, and it should act only on what
the container itself can fix: Kubernetes' liveness probe is shallow by design,
because restarting a healthy process when the database is down makes an outage
worse. Its readiness probe may be deeper, because "don't send me traffic" is a
safe thing to say when a dependency is gone.

A monitor like Pulse wants the deep view, but it has to judge rather than obey.
Deep checks report every component, and a laptop deployment or a partial outage
will show components failing that users never notice: file storage, migrations,
a Celery queue. If Pulse marked `shop` down whenever `/ht/` said 500, every such
component would become a page. That is why a monitor can carry **JSON
assertions**: Pulse asks `/ht/` for JSON, ignores the status code, and checks only
the keys the operator listed, `Database(alias='default')` and the Redis entry
for `shop`. The deep check still tells the truth about every component; Pulse
decides which truths matter. Stopping `shop-redis` turns both the cache and
Redis entries red and the assertion fails, while `/health` stays 200: the demo
for Checkpoint 1.

So when should a health check say "down"? When the service cannot do its job for
users. Dependencies on the main request path belong in the deep check. Optional
or background components belong in the report but should not flip the verdict,
and the thing reading the check should be able to say which is which. The
ticket's common mistake is the right summary: asserting every key is just the
deep check again.

**Read real code.** django-health-check's `HealthCheckView` (`health_check/views.py`)
runs every check concurrently with a per-check timeout, answers 500 if any raised,
and renders JSON as one key per check using `repr(check)`, which is why the keys
look like `Database(alias='default')`. The Redis check in `health_check/contrib/redis.py`
just calls `ping()` and maps connection errors to `ServiceUnavailable`. Kubernetes'
probe docs are the clearest statement of the liveness/readiness split.
