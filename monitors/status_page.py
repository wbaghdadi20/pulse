"""The public status page: each public monitor's state, 90-day uptime and latest incidents.

Why it is cached this way: docs/adr/0009-status-page-cache.md
"""

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, QuerySet, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from . import uptime
from .models import Check, Incident, Monitor

WINDOW_DAYS = 90
RECENT_INCIDENTS = 3
CACHE_KEY = "status:page"


def get_status_page() -> dict[str, Any]:
    """Cache-aside: the cached page if there is one, else build it and keep it a while."""
    page: dict[str, Any] | None = cache.get(CACHE_KEY)
    if page is None:
        page = build_status_page()
        cache.set(CACHE_KEY, page, settings.PULSE_STATUS_CACHE_SECONDS)
    return page


def invalidate() -> None:
    """Drop the cached page once the current transaction has committed.

    Not before: a request arriving in between would rebuild the page from the old
    rows and cache that for a full TTL.
    """
    transaction.on_commit(lambda: cache.delete(CACHE_KEY))


def build_status_page(now: datetime | None = None) -> dict[str, Any]:
    """Four queries, however many monitors there are. Only is_public monitors,
    whoever owns them: nothing about a private one is read, let alone shown."""
    now = now or timezone.now()
    since = now - timedelta(days=WINDOW_DAYS)

    stats = {
        row["monitor_id"]: row
        for row in Check.objects.filter(monitor__is_public=True, checked_at__gt=since)
        .values("monitor_id")
        .annotate(**uptime.aggregates())
    }
    down = set(
        Incident.objects.filter(
            monitor__is_public=True, resolved_at__isnull=True
        ).values_list("monitor_id", flat=True)
    )
    incidents: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for incident in _recent_incidents():
        incidents[incident.monitor_id].append(
            {
                "id": incident.pk,
                "kind": incident.kind,
                "cause": incident.cause,
                "opened_at": incident.opened_at,
                "resolved_at": incident.resolved_at,
            }
        )
    # Monitors last: one created or deleted while the queries above ran is then
    # simply absent from them, rather than a key that is missing.
    monitors = [
        {
            "id": monitor["id"],
            "name": monitor["name"],
            "state": "down" if monitor["id"] in down else "up",
            **_uptime(stats.get(monitor["id"])),
            "incidents": incidents.get(monitor["id"], []),
        }
        for monitor in Monitor.objects.filter(is_public=True)
        .order_by("name", "id")
        .values("id", "name")
    ]
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
        Incident.objects.filter(monitor__is_public=True)
        .annotate(position=newest_first)
        .filter(position__lte=RECENT_INCIDENTS)
        .order_by("monitor_id", "position")
    )
