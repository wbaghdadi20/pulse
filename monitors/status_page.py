"""The public status page: each public monitor's state, 90-day uptime and latest incidents.

Why it is cached this way: docs/adr/0009-status-page-cache.md
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q, QuerySet, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from orgs.tenancy import current_schema, in_schema

from . import uptime
from .models import Incident, Monitor

WINDOW_DAYS = 90
RECENT_INCIDENTS = 3
CACHE_KEY = "status:page"


def get_status_page() -> tuple[dict[str, Any], str]:
    """The page and its ETag. Cache-aside: the cached pair if there is one, else
    build it and keep it a while."""
    cached: tuple[dict[str, Any], str] | None = cache.get(CACHE_KEY)
    if cached is None:
        page = build_status_page()
        cached = (page, etag_of(page))
        cache.set(CACHE_KEY, cached, settings.PULSE_STATUS_CACHE_SECONDS)
    return cached


def etag_of(page: dict[str, Any]) -> str:
    """The same for two pages that say the same about the monitors.

    generated_at is left out: it changes every time the page is rebuilt, and a
    rebuilt page that says nothing new should still be "not modified". Two such
    pages are equivalent, not byte for byte identical, which is what a weak
    validator (W/) means.
    """
    monitors = json.dumps(page["monitors"], sort_keys=True, default=str)
    return f'W/"{hashlib.sha256(monitors.encode()).hexdigest()[:32]}"'


def invalidate() -> None:
    """Drop the cached page once the current transaction has committed.

    Not before: a request arriving in between would rebuild the page from the old
    rows and cache that for a full TTL.
    """
    schema = current_schema()  # the page of the organization that changed

    def drop() -> None:
        with in_schema(schema):
            cache.delete(CACHE_KEY)

    transaction.on_commit(drop)


def build_status_page(now: datetime | None = None) -> dict[str, Any]:
    """Seven queries at most, however many monitors there are. Only is_public monitors,
    whoever owns them: nothing about a private one is read, let alone shown."""
    now = now or timezone.now()

    stats = uptime.totals(Q(monitor__is_public=True), WINDOW_DAYS, now)
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


def _uptime(totals: uptime.Totals | None) -> dict[str, Any]:
    summary = uptime.summarise(totals)
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
