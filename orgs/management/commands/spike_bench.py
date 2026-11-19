"""Spike measurements for docs/adr/0026. Throwaway: run in a database you can lose."""

import statistics
import time
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.test.utils import override_settings
from django.utils import timezone

from accounts.models import User
from monitors import tasks
from monitors.models import Monitor
from orgs import services
from orgs.models import Organization
from orgs.tenancy import use_public

BENCH_EMAIL = "bench@example.com"


def _owner() -> User:
    return User.objects.filter(email=BENCH_EMAIL).first() or User.objects.create_user(
        BENCH_EMAIL
    )


def _report(label: str, seconds: list[float]) -> str:
    ms = [round(s * 1000, 1) for s in seconds]
    return f"{label}: median {statistics.median(ms)} ms, min {min(ms)}, max {max(ms)}, runs {ms}"


class Command(BaseCommand):
    help = "create: time creating tenants; seed: N tenants with due monitors; tick: time the tick."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("action", choices=["create", "seed", "tick", "drop"])
        parser.add_argument("--mode", choices=["migrate", "clone"], default="clone")
        parser.add_argument("--count", type=int, default=5)
        parser.add_argument("--tenants", type=int, default=100)
        parser.add_argument("--monitors", type=int, default=3)
        parser.add_argument("--runs", type=int, default=5)
        parser.add_argument("--prefix", default="bench")

    def handle(self, *args: Any, **opts: Any) -> None:
        getattr(self, "_" + opts["action"])(opts)

    def _create(self, opts: dict[str, Any]) -> None:
        clone = opts["mode"] == "clone"
        owner = _owner()
        times = []
        with override_settings(TENANT_CREATION_FAKES_MIGRATIONS=clone):
            for i in range(opts["count"]):
                slug = f"{opts['prefix']}-{opts['mode']}-{int(time.time() * 1000)}-{i}"
                started = time.perf_counter()
                services.create_organization(slug, slug, owner)
                times.append(time.perf_counter() - started)
        self.stdout.write(_report(f"create ({opts['mode']})", times))

    def _seed(self, opts: dict[str, Any]) -> None:
        owner = _owner()
        have = Organization.objects.filter(
            slug__startswith=opts["prefix"] + "-t"
        ).count()
        for i in range(have, opts["tenants"]):
            org = services.create_organization(
                f"t{i}", f"{opts['prefix']}-t{i:03d}", owner
            )
            with org:
                Monitor.objects.bulk_create(
                    Monitor(
                        owner=owner,
                        name=f"m{j}",
                        url="http://target:8000/health",
                        interval_s=10,
                        timeout_s=5,
                    )
                    for j in range(opts["monitors"])
                )
        total = Organization.objects.exclude(schema_name="public").count()
        self.stdout.write(f"tenants (not public): {total}")

    def _tick(self, opts: dict[str, Any]) -> None:
        orgs = services.active_tenants()
        all_due, none_due = [], []
        for _ in range(opts["runs"]):
            self._make_all_due(orgs)
            started = time.perf_counter()
            queued = tasks.enqueue_due_checks()
            all_due.append(time.perf_counter() - started)
            started = time.perf_counter()
            again = tasks.enqueue_due_checks()
            none_due.append(time.perf_counter() - started)
        self.stdout.write(
            f"{len(orgs)} tenants, {queued} queued per tick, then {again}"
        )
        self.stdout.write(_report("tick, every monitor due", all_due))
        self.stdout.write(_report("tick, nothing due", none_due))

    @staticmethod
    def _make_all_due(orgs: list[Organization]) -> None:
        past = timezone.now() - timedelta(seconds=1)
        for org in orgs:
            with org:
                Monitor.objects.update(next_check_at=past)
        use_public()

    def _drop(self, opts: dict[str, Any]) -> None:
        for org in Organization.objects.filter(slug__startswith=opts["prefix"] + "-"):
            org.delete(force_drop=True)
