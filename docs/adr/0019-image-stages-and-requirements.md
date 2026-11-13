# 19. One Dockerfile with a dev and a runtime target, and requirements split the same way

- Status: accepted
- Date: 2026-11-12 (D9)

## Context

Until D9 one `requirements.txt` held everything, and the one image installed all
of it and copied the whole repository. The image that would be published carried
mypy, pytest, hypothesis, mutmut, the tests, the docs and three other apps
(`shop/`, `flaky/`, `catcher/`). The tests run inside that image during
development, so the tools cannot simply be removed.

## Options for the dependencies

1. **One file** (before). Nothing to keep in sync; no way to install less.
2. **Two files kept by hand** (chosen): `requirements.txt` is what the app imports
   and what those packages need; `requirements-dev.txt` starts with
   `-r requirements.txt` and adds the tools. Every version is pinned in both.
3. **A lock generated from a short list** (pip-tools, uv). The split is computed,
   not kept. Worth it once dependencies change weekly; one more tool until then.

## Options for the image

1. **Two Dockerfiles.** The base, the user and the install step exist twice and drift.
2. **One Dockerfile, several stages** (chosen): `builder` installs the runtime
   packages into `/venv`; `runtime` copies `/venv` and the code onto a clean base;
   `dev` continues from `builder` and adds the tools and the repository.

## Decision

- `runtime` is the last stage, so `docker build .` produces it. `compose.yaml`, CI
  and the release build it. `compose.override.yaml` builds `dev`.
- The packages travel between stages as a virtual environment: one directory to
  copy, found through `PATH`.
- `runtime` copies `manage.py`, `gunicorn.conf.py` and the four packages by name.
  Nothing else in the folder can reach a published image.
- Both targets run as the user `app` (uid 1000). `runtime` starts gunicorn with
  the exec form of `CMD`, so gunicorn is PID 1 and receives SIGTERM itself. `dev`
  has no `CMD`: Compose gives each service its command.
- gunicorn's settings are in `gunicorn.conf.py`; `WEB_CONCURRENCY` sets the workers.

## Consequences

- Size, from `docker images`: 541 MB on disk (106 MB compressed) before, 386 MB
  (73.8 MB) for `runtime`. The difference is the dev packages (mypy alone is
  61 MB) and the files no longer copied. Every dependency installs as a wheel, so
  the builder holds no compilers and the stages save nothing by themselves: they
  are what lets one file produce two images.
- Tests always run against the versions that ship, because the dev file includes
  the runtime file.
- A package filed on the wrong side is invisible to the tests. It happened in this
  change: kombu's Redis transport imports `packaging` but declares it only under
  its `redis` extra, and it had been present because pytest and mypy need it too.
  The worker failed to start from `runtime`. CI now runs the built image:
  migrate, API, signup, login and a worker ping.
- A new Django app must be added to the `COPY` lines. Forgetting it fails the
  same CI step.
- The base image's own packages are not pinned beyond `python:3.14-slim`.
- The image is built for linux/amd64 only.
