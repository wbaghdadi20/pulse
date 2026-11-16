"""What the uptime endpoint costs the database."""

import pytest
from django.urls import reverse

from monitors.models import Check

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("checks", [1, 300])
def test_uptime_takes_three_queries_however_many_checks(
    api, monitor, django_assert_num_queries, checks
):
    Check.objects.bulk_create(
        Check(monitor=monitor, ok=True, status_code=200, latency_ms=12)
        for _ in range(checks)
    )

    with django_assert_num_queries(3):  # the token's user, the monitor, the aggregate
        body = api.get(reverse("monitor-uptime", args=[monitor.pk])).json()

    assert body["checks"] == checks
