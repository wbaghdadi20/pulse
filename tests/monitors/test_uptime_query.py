"""What the uptime endpoint costs the database: how many queries, and read from where."""

from datetime import timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from monitors import uptime
from monitors.models import Check

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("checks", [1, 300])
def test_uptime_takes_four_queries_however_many_checks(
    api, monitor, django_assert_num_queries, checks
):
    Check.objects.bulk_create(
        Check(monitor=monitor, ok=True, status_code=200, latency_ms=12)
        for _ in range(checks)
    )

    # SET search_path into the organization, the token's user with their membership,
    # the monitor, the aggregate
    with django_assert_num_queries(4):
        body = api.get(reverse("monitor-uptime", args=[monitor.pk])).json()

    assert body["checks"] == checks


def test_the_uptime_aggregates_are_answered_from_the_index_alone(monitor):
    """An aggregate over a column the index does not carry would send Postgres back
    to the table for every check, and this plan would say Index Scan instead."""
    with connection.cursor() as cursor:
        # A table this small is read whole unless the planner is told not to.
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")

    plan = (
        Check.objects.filter(
            monitor=monitor, checked_at__gt=timezone.now() - timedelta(days=30)
        )
        .values("monitor_id")
        .annotate(**uptime.aggregates())
        .explain()
    )

    assert "Index Only Scan using checks_monitor_checked_cover" in plan
