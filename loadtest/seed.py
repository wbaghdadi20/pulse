"""Fills the load-test database. Run once, against pulse_loadtest (see LOADTEST.md):

    docker compose -f compose.yaml -f loadtest/compose.loadtest.yaml \
        exec -T api python manage.py shell < loadtest/seed.py

50 accounts with 10 monitors each. Every monitor has a check every 5 minutes for the
last 30 days; the 20 public ones have 90 days, the window of the status page.
"""

from django.contrib.auth.hashers import make_password
from django.db import connection

from accounts.models import User
from monitors.models import Monitor

ACCOUNTS = 50
MONITORS_EACH = 10
PUBLIC_MONITORS = 20
PASSWORD = "loadtest-only-not-a-secret"

if User.objects.filter(email__startswith="loadtest-").exists():
    raise SystemExit("Already seeded.")

# One hash for every account: Argon2 fifty times over would only slow the seed.
password = make_password(PASSWORD)
users = User.objects.bulk_create(
    User(email=f"loadtest-{n:02}@example.com", password=password)
    for n in range(ACCOUNTS)
)
monitors = Monitor.objects.bulk_create(
    Monitor(
        owner=user,
        name=f"service-{n}",
        url="http://flaky:8000/health",
        timeout_s=5,
        interval_s=300,
    )
    for user in users
    for n in range(MONITORS_EACH)
)
# Spread over the accounts, so no single account owns the whole status page.
public = [monitor.pk for monitor in monitors[:: len(monitors) // PUBLIC_MONITORS]]
Monitor.objects.filter(pk__in=public).update(is_public=True)

with connection.cursor() as cursor:
    # ORDER BY the time, not the monitor: rows land in the table in the order a
    # running Pulse writes them, one monitor's checks scattered among the others'.
    # Seeded monitor by monitor, each one's rows would sit on neighbouring pages
    # and every query for one monitor would look cheaper than it is.
    cursor.execute(
        """
        INSERT INTO checks (monitor_id, checked_at, ok, status_code, latency_ms, error_type)
        SELECT id, at, ok,
               CASE WHEN ok THEN 200 END,
               CASE WHEN ok THEN 20 + (random() * 180)::int ELSE 5000 END,
               CASE WHEN NOT ok THEN 'timeout' END
        FROM (
            SELECT m.id, at, random() > 0.01 AS ok
            FROM monitors m
            CROSS JOIN LATERAL generate_series(
                now() - CASE WHEN m.is_public THEN interval '90 days' ELSE interval '30 days' END,
                now(),
                interval '5 minutes'
            ) AS at
        ) AS history
        ORDER BY at
        """
    )
    checks = cursor.rowcount
    cursor.execute(
        """
        INSERT INTO incidents (monitor_id, kind, opened_at, resolved_at, cause)
        SELECT m.id, 'availability', opened_at, opened_at + interval '20 minutes',
               '3 failed checks in a row'
        FROM monitors m
        CROSS JOIN generate_series(
            now() - interval '80 days', now() - interval '10 days', interval '14 days'
        ) AS opened_at
        WHERE m.is_public
        """
    )
    incidents = cursor.rowcount
    # Fresh planner statistics: autovacuum would get to it, but not before the first run.
    cursor.execute("ANALYZE")

print(
    f"{len(users)} accounts, {len(monitors)} monitors ({len(public)} public), "
    f"{checks} checks, {incidents} incidents"
)
