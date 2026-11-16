from django.contrib.postgres.operations import (
    AddIndexConcurrently,
    RemoveIndexConcurrently,
)
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction. It builds the index without
    # blocking the workers' inserts into checks, which a plain CREATE INDEX would.
    atomic = False

    dependencies = [
        ("monitors", "0010_check_error_type_blocked"),
    ]

    operations = [
        # The new index first: there is no moment with no index on checks.
        AddIndexConcurrently(
            model_name="check",
            index=models.Index(
                fields=["monitor", "checked_at"],
                include=["ok", "latency_ms"],
                name="checks_monitor_checked_cover",
            ),
        ),
        RemoveIndexConcurrently(
            model_name="check",
            name="checks_monitor_checked_idx",
        ),
    ]
