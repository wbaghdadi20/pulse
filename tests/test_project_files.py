"""Numbers that are written in two files and have to agree. Each test names the pair."""

import re
import runpy
from pathlib import Path
from typing import Any

import yaml

from monitors.tasks import CHECK_TIME_LIMIT_S

ROOT = Path(__file__).resolve().parent.parent


def load(name: str) -> Any:
    return yaml.safe_load((ROOT / name).read_text())


def stop_grace_seconds(service: str) -> int:
    written = load("compose.yaml")["services"][service]["stop_grace_period"]
    match = re.fullmatch(r"(\d+)s", written)
    assert match, f"{service}: stop_grace_period {written!r} is not whole seconds"
    return int(match[1])


def test_the_worker_gets_longer_to_stop_than_a_check_may_run():
    """Shorter, and `docker compose stop` kills a check that Celery was letting finish."""
    assert stop_grace_seconds("worker") > CHECK_TIME_LIMIT_S


def test_the_api_gets_longer_to_stop_than_gunicorn_waits_for_requests():
    gunicorn = runpy.run_path(str(ROOT / "gunicorn.conf.py"))

    assert stop_grace_seconds("api") > gunicorn["graceful_timeout"]


def test_ci_lints_with_the_ruff_version_the_pre_commit_hook_runs():
    (hook,) = [
        repo
        for repo in load(".pre-commit-config.yaml")["repos"]
        if repo["repo"].endswith("/ruff-pre-commit")
    ]
    pins = (ROOT / "requirements-dev.txt").read_text().split()

    assert f"ruff=={hook['rev'].removeprefix('v')}" in pins
