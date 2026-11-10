from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection, transaction

from monitors.models import Monitor

# One INSERT ... SELECT over generate_series: Postgres builds every row itself,
# which takes seconds. Inserting rows one by one from Python would take minutes.
INSERT_FAKE_CHECKS = """
    WITH fake AS (
        SELECT
            (%(monitor_ids)s::bigint[])[1 + mod(n, %(monitor_count)s)] AS monitor_id,
            now() - random() * interval '90 days' AS checked_at,
            random() < 0.98 AS ok
        FROM generate_series(1, %(count)s) AS n
    )
    INSERT INTO checks (monitor_id, checked_at, ok, status_code, latency_ms, error_type)
    SELECT
        monitor_id,
        checked_at,
        ok,
        CASE WHEN ok THEN 200 ELSE 503 END,
        (20 + random() * 300)::int,
        CASE WHEN ok THEN NULL ELSE 'wrong_status' END
    FROM fake
"""


class Command(BaseCommand):
    help = "Insert fake check results for seed monitors, spread over the last 90 days."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--count", type=int, default=500_000)
        parser.add_argument("--monitors", type=int, default=10)

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        count: int = options["count"]
        monitors: int = options["monitors"]
        monitor_ids = [
            Monitor.objects.get_or_create(
                name=f"seed-{i}",
                defaults={
                    "url": f"http://seed-{i}.invalid/",
                    "timeout_s": 5,
                    "interval_s": 60,
                },
            )[0].pk
            for i in range(1, monitors + 1)
        ]
        with connection.cursor() as cursor:
            cursor.execute(
                INSERT_FAKE_CHECKS,
                {
                    "monitor_ids": monitor_ids,
                    "monitor_count": len(monitor_ids),
                    "count": count,
                },
            )
            # Refresh the planner's statistics so EXPLAIN reflects the new rows.
            cursor.execute("ANALYZE checks")
        self.stdout.write(
            self.style.SUCCESS(f"Inserted {count} checks for {monitors} seed monitors.")
        )
