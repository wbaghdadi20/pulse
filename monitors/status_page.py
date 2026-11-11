"""The public status page: each monitor's state, 90-day uptime and latest incidents."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from django.db.models import F, QuerySet, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from . import uptime
from .models import Check, Incident, Monitor

WINDOW_DAYS = 90
RECENT_INCIDENTS = 3


def build_status_page(now: datetime | None = None) -> dict[str, Any]:
    """Four queries, however many monitors there are."""
    now = now or timezone.now()
    since = now - timedelta(days=WINDOW_DAYS)

    stats = {
        row["monitor_id"]: row
        for row in Check.objects.filter(checked_at__gt=since)
        .values("monitor_id")
        .annotate(**uptime.aggregates())
    }
    down = set(
        Incident.objects.filter(resolved_at__isnull=True).values_list(
            "monitor_id", flat=True
        )
    )
    monitors = [
        {
            "id": monitor["id"],
            "name": monitor["name"],
            "state": "down" if monitor["id"] in down else "up",
            **_uptime(stats.get(monitor["id"])),
            "incidents": [],
        }
        for monitor in Monitor.objects.order_by("name", "id").values("id", "name")
    ]
    by_id = {monitor["id"]: monitor for monitor in monitors}
    for incident in _recent_incidents():
        by_id[incident.monitor_id]["incidents"].append(
            {
                "id": incident.pk,
                "kind": incident.kind,
                "cause": incident.cause,
                "opened_at": incident.opened_at,
                "resolved_at": incident.resolved_at,
            }
        )
    return {"generated_at": now, "window_days": WINDOW_DAYS, "monitors": monitors}


def _uptime(stats: Mapping[str, Any] | None) -> dict[str, Any]:
    summary = uptime.summarise(stats)
    return {
        "uptime_percent": summary["uptime_percent"],
        "p95_latency_ms": summary["p95_latency_ms"],
    }


def _recent_incidents() -> QuerySet[Incident]:
    """Each monitor's latest incidents, newest first, in one query (a window function)."""
    newest_first = Window(
        RowNumber(),
        partition_by=F("monitor_id"),
        order_by=[F("opened_at").desc(), F("id").desc()],
    )
    return (
        Incident.objects.annotate(position=newest_first)
        .filter(position__lte=RECENT_INCIDENTS)
        .order_by("monitor_id", "position")
    )
