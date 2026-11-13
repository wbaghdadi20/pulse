# 20. Every pull request passes CI; a version tag publishes the image

- Status: accepted
- Date: 2026-11-12 (D9)

## Context

Until D9 the checks (ruff, mypy, pytest) ran when the author remembered, on the
author's machine, and "the version of Pulse" meant whatever `main` was that day.

## Options for the pipeline's shape

1. **One job per check, in parallel.** Fastest. Each job installs the
   dependencies again, and tests run even when the linter has already failed.
2. **One job, steps in order** (chosen): ruff, mypy, migrations check, pytest,
   docker build, run the image. The cheap checks fail first.

## Options for where the tests run

1. **Inside Compose on the runner**, as in development. One way of running; a
   slower start and a second layer to debug.
2. **On the runner, with Postgres and Redis as service containers** (chosen, and
   the ticket's). They are on `localhost` there, not under their Compose names,
   which works only because every host comes from an environment variable.

## Options for what a release is

1. **Every push to `main` publishes `latest`.** No one can say which version is
   running or go back to the one before.
2. **A semantic version tag publishes that version** (chosen): `v0.1.0` becomes
   `ghcr.io/wbaghdadi20/pulse:v0.1.0` and `:latest`.
3. **Publish from CI on `main`, and have the tag only re-label that image.** The
   image that was tested is the image that ships, byte for byte. Needs a push on
   every commit and a second registry step. The right next move.

## Decision

- `ci.yml` runs on every pull request and every push to `main`, with
  `HYPOTHESIS_PROFILE=ci`: 2000 examples per property, no deadline per example
  (a shared runner can stall one), and the failing example printed (CI keeps no
  example database).
- Its last step runs the image it built, not only builds it (ADR 0019).
- The check `ci` is required on `main`, for administrators too.
- `release.yml` runs on tags matching `v*.*.*`. It calls `ci.yml` on the tagged
  commit, then builds and pushes. It logs in with the workflow's own
  `GITHUB_TOKEN`; only that job has `packages: write`.
- A pre-release tag (`v0.2.0-rc.1`) is published under its own name and does not
  move `latest`.
- `compose.release.yaml` runs api, worker and beat from a published version.

## Consequences

- A release is rebuilt from the tagged commit, not promoted from the CI build
  (option 3). The base image or a wheel could differ between the two builds.
- Nothing stops a tag on a commit that is not on `main`. CI still has to pass on it.
- A tag is never moved: a published version would change under whoever runs it.
  A bad release is followed by the next patch version.
- Rolling back means running the previous tag. It does not undo migrations the
  newer version applied.
- The actions are pinned to major versions (`actions/checkout@v7`), as GitHub's
  guides show. Pinning to commit hashes would protect against a moved tag, at the
  cost of updates by hand or by Dependabot.
- The mutation tests are not in CI: minutes per run, and a score to read, not a
  pass or fail.
- A new package on ghcr.io is private until its visibility is changed by hand.
