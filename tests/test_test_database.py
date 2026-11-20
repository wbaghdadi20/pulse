"""The `committed` fixture writes without a django_db mark, so pytest-django alone
would not set up a test database for it: run alone, a test using it would write to
the development database. Run alone here, against a development database that does
not exist, it still passes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ALONE = {
    # Each its own database name: two at once would share one test database.
    "pulse_absent_1": "tests/monitors/test_claim.py"
    "::test_what_a_committed_test_writes_another_connection_sees",
}


@pytest.mark.parametrize(("database", "test"), ALONE.items(), ids=["committed"])
def test_a_committing_test_run_alone_uses_a_test_database(database, test):
    # Any write to the "development" database would fail: it does not exist.
    env = {**os.environ, "POSTGRES_DB": database}
    env.pop("PYTEST_XDIST_WORKER", None)

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            test,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
    assert "1 passed" in run.stdout
