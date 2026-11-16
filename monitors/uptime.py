"""Uptime and latency, computed by Postgres. Used by the uptime endpoint and the status page."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Aggregate, Count, Q, QuerySet, Sum
from django.db.models.expressions import RawSQL
from django.utils import timezone

from core.db import PercentileCont

from . import latency, retention
from .models import Check, CheckHour

P95 = 0.95


@dataclass(frozen=True)
class Totals:
    """What a monitor's checks over some stretch of time add up to. At least one check."""

    checks: int
    failures: int
    p95_latency_ms: float


def aggregates() -> dict[str, Aggregate]:
    """Over raw checks. Only ok and latency_ms are read: both are in the index the
    query runs on, so Postgres never visits the table (count(*), not count(id))."""
    return {
        "total": Count("*"),
        "failed": Count("ok", filter=Q(ok=False)),
        "p95": PercentileCont("latency_ms", P95),
    }


def totals(of: Q, days: int, now: datetime | None = None) -> dict[int, Totals]:
    """Per monitor id, over the last `days` days. `of` picks the monitors: it
    filters checks and check hours alike, e.g. Q(monitor=monitor).

    A window the raw checks still cover is computed from them and is exact. A
    longer one is the hourly sums from the top of its first hour, plus the raw
    checks newer than the last hour summed up; its p95 comes from latency buckets
    and is within 5% of the real one.
    """
    now = now or timezone.now()
    since = now - timedelta(days=days)
    rolled = None
    if days > settings.PULSE_RAW_CHECK_DAYS:
        rolled = retention.rolled_up_until()
    if rolled is None or rolled <= since:
        return _exact(Check.objects.filter(of, checked_at__gt=since))
    return _summed(
        CheckHour.objects.filter(
            of, hour__gte=retention.start_of_hour(since), hour__lt=rolled
        ),
        Check.objects.filter(of, checked_at__gte=rolled),
    )


def summarise(totals: Totals | None) -> dict[str, Any]:
    """Rounded for display. No checks: nulls, not zeros."""
    if totals is None:
        return {"uptime_percent": None, "p95_latency_ms": None, "checks": 0}
    up = totals.checks - totals.failures
    return {
        "uptime_percent": round(up / totals.checks * 100, 2),
        "p95_latency_ms": round(totals.p95_latency_ms),
        "checks": totals.checks,
    }


def _exact(checks: QuerySet[Check]) -> dict[int, Totals]:
    over = aggregates()
    rows = checks.values("monitor_id").annotate(
        total=over["total"], failed=over["failed"], p95=over["p95"]
    )
    return {
        row["monitor_id"]: Totals(row["total"], row["failed"], row["p95"])
        for row in rows
    }


def _summed(hours: QuerySet[CheckHour], recent: QuerySet[Check]) -> dict[int, Totals]:
    """Hours already summed up and the checks since, added together: three queries."""
    checks: Counter[int] = Counter()
    failures: Counter[int] = Counter()
    buckets: defaultdict[int, Counter[int]] = defaultdict(Counter)

    for row in hours.values("monitor_id").annotate(
        total=Sum("checks"), failed=Sum("failures")
    ):
        checks[row["monitor_id"]] += row["total"]
        failures[row["monitor_id"]] += row["failed"]
    for monitor_id, bucket, count in _bucket_sums(hours):
        buckets[monitor_id][bucket] += count
    for row in (
        recent.annotate(bucket=RawSQL(latency.BUCKET_SQL, []))
        .values("monitor_id", "bucket")
        .annotate(total=Count("*"), failed=Count("ok", filter=Q(ok=False)))
    ):
        checks[row["monitor_id"]] += row["total"]
        failures[row["monitor_id"]] += row["failed"]
        buckets[row["monitor_id"]][row["bucket"]] += row["total"]

    return {
        monitor_id: Totals(
            checks[monitor_id],
            failures[monitor_id],
            latency.percentile(buckets[monitor_id], P95),
        )
        for monitor_id in checks
    }


def _bucket_sums(hours: QuerySet[CheckHour]) -> list[tuple[int, int, int]]:
    """(monitor id, bucket, checks) with each hour's latency_buckets added up.

    Postgres unpacks the JSON and adds it up, so one row per monitor and bucket
    comes back instead of every hour. The ORM builds the inner query (which hours);
    it has no way to say jsonb_each_text.
    """
    inner, params = hours.values(
        "monitor_id", "latency_buckets"
    ).query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hours.monitor_id, entry.key::int, sum(entry.value::int)::int "
            f"FROM ({inner}) AS hours, jsonb_each_text(hours.latency_buckets) AS entry "
            "GROUP BY 1, 2",
            params,
        )
        return list(cursor.fetchall())
