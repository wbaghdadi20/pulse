"""Data retention for checks: every hour is summed up into check_hours, then the
raw rows older than PULSE_RAW_CHECK_DAYS are deleted. The hourly rows are deleted
too once no window reads them, so neither table grows with time.

Why, and what it costs: docs/adr/0024-check-retention.md
"""

from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Min
from django.utils import timezone

from .latency import BUCKET_SQL
from .models import Check, CheckHour

HOUR = timedelta(hours=1)
# A check's row is written when its probe has finished. An hour is summed up only
# once it has been over for this long, so none of its checks is still on its way in.
SETTLE = timedelta(minutes=5)
# One day more than the longest window anything reads: 90 days, on the status page
# and the uptime endpoint. A test holds the two together.
HOURS_KEPT = timedelta(days=91)

# One statement, so the sums never leave Postgres and are taken from one snapshot.
# Run again over the same hours it replaces their rows with the same numbers.
ROLL_UP = f"""
    INSERT INTO check_hours (monitor_id, hour, checks, failures, latency_buckets)
    SELECT monitor_id, hour, sum(n), sum(failed), jsonb_object_agg(bucket, n)
    FROM (
        SELECT monitor_id,
               date_trunc('hour', checked_at, 'UTC') AS hour,
               {BUCKET_SQL} AS bucket,
               count(*) AS n,
               count(*) FILTER (WHERE NOT ok) AS failed
        FROM checks
        WHERE checked_at >= %s AND checked_at < %s
        GROUP BY 1, 2, 3
    ) AS per_bucket
    GROUP BY monitor_id, hour
    ON CONFLICT (monitor_id, hour) DO UPDATE
    SET checks = EXCLUDED.checks,
        failures = EXCLUDED.failures,
        latency_buckets = EXCLUDED.latency_buckets
"""


def start_of_hour(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def rolled_up_until() -> datetime | None:
    """Every hour before this moment is in check_hours. None: nothing is yet.

    The newest row marks it: roll_up() sums hours up oldest first and in one
    statement, so no hour before the newest one is missing.
    """
    newest = CheckHour.objects.order_by("-hour").values_list("hour", flat=True).first()
    return None if newest is None else newest + HOUR


def roll_up(now: datetime | None = None) -> int:
    """Sum up every finished hour not summed up yet. Returns the rows written."""
    until = start_of_hour((now or timezone.now()) - SETTLE)
    start = rolled_up_until()
    if start is None:
        oldest = Check.objects.aggregate(oldest=Min("checked_at"))["oldest"]
        if oldest is None:
            return 0
        start = start_of_hour(oldest)
    if start >= until:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(ROLL_UP, [start, until])
        return int(cursor.rowcount)


def prune(now: datetime | None = None) -> int:
    """Delete the checks kept long enough, never one whose hour is not summed up."""
    rolled = rolled_up_until()
    if rolled is None:
        return 0
    kept_from = start_of_hour(
        (now or timezone.now()) - timedelta(days=settings.PULSE_RAW_CHECK_DAYS)
    )
    deleted, _ = Check.objects.filter(checked_at__lt=min(rolled, kept_from)).delete()
    return deleted


def forget(now: datetime | None = None) -> int:
    """Delete the hourly rows older than any window reads."""
    oldest_read = start_of_hour((now or timezone.now()) - HOURS_KEPT)
    deleted, _ = CheckHour.objects.filter(hour__lt=oldest_read).delete()
    return deleted
