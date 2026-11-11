"""Uptime and latency, computed by Postgres. Used by the uptime endpoint and the status page."""

from collections.abc import Mapping
from typing import Any

from django.db.models import Aggregate, Avg, Count, FloatField, IntegerField
from django.db.models.functions import Cast

from core.db import PercentileCont


def aggregates() -> dict[str, Aggregate]:
    return {
        # avg(ok::int): true counts as 1, false as 0, so the average is the up ratio.
        "up_ratio": Avg(Cast("ok", IntegerField()), output_field=FloatField()),
        "p95_latency_ms": PercentileCont("latency_ms", 0.95),
        "checks": Count("id"),
    }


def summarise(stats: Mapping[str, Any] | None) -> dict[str, Any]:
    """The aggregates rounded for display. No checks: nulls, not zeros."""
    if stats is None or stats["checks"] == 0:
        return {"uptime_percent": None, "p95_latency_ms": None, "checks": 0}
    return {
        "uptime_percent": round(stats["up_ratio"] * 100, 2),
        "p95_latency_ms": round(stats["p95_latency_ms"]),
        "checks": stats["checks"],
    }
