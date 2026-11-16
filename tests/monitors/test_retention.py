"""Data retention: hours summed up into check_hours, raw checks deleted after their
keep, and uptime that still adds up once they are gone."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from monitors import latency, retention, status_page, tasks, uptime
from monitors.models import Check, CheckHour, Monitor
from monitors.serializers import UPTIME_WINDOWS_DAYS
from tests.conftest import create_monitor

pytestmark = pytest.mark.django_db

# Half past twelve: the hour from 12:00 is still running, the one from 11:00 is over.
NOW = datetime(2026, 11, 10, 12, 30, tzinfo=UTC)
ELEVEN = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)


def add_check(
    monitor: Monitor, at: datetime, *, ok: bool = True, latency_ms: int = 100
) -> Check:
    return Check.objects.create(
        monitor=monitor,
        checked_at=at,
        ok=ok,
        status_code=200 if ok else 500,
        latency_ms=latency_ms,
        error_type=None if ok else "wrong_status",
    )


def hours_of(monitor: Monitor) -> list[tuple[datetime, int, int, dict[str, int]]]:
    return list(
        CheckHour.objects.filter(monitor=monitor)
        .order_by("hour")
        .values_list("hour", "checks", "failures", "latency_buckets")
    )


# The bucket a latency of 100 ms is counted in (monitors/latency.py), as stored.
B100 = "48"


# --- rolling up ---------------------------------------------------------------------


def test_an_hour_becomes_one_row_with_its_count_failures_and_latencies(monitor):
    add_check(monitor, ELEVEN + timedelta(minutes=5), latency_ms=100)
    add_check(monitor, ELEVEN + timedelta(minutes=25), latency_ms=101)
    add_check(monitor, ELEVEN + timedelta(minutes=35), latency_ms=200)
    add_check(monitor, ELEVEN + timedelta(minutes=45), ok=False, latency_ms=300)

    assert retention.roll_up(NOW) == 1

    assert hours_of(monitor) == [(ELEVEN, 4, 1, {B100: 2, "55": 1, "59": 1})]


def test_each_monitor_and_each_hour_gets_its_own_row(monitor):
    other = create_monitor(name="other")
    ten = ELEVEN - timedelta(hours=1)
    add_check(monitor, ten)
    add_check(monitor, ELEVEN)
    add_check(monitor, ELEVEN + timedelta(minutes=59, seconds=59))
    add_check(other, ELEVEN, ok=False)

    assert retention.roll_up(NOW) == 3

    assert hours_of(monitor) == [(ten, 1, 0, {B100: 1}), (ELEVEN, 2, 0, {B100: 2})]
    assert hours_of(other) == [(ELEVEN, 1, 1, {B100: 1})]


def test_the_hour_still_running_is_left_alone(monitor):
    add_check(monitor, NOW - timedelta(minutes=20))

    assert retention.roll_up(NOW) == 0
    assert hours_of(monitor) == []


def test_an_hour_is_summed_up_only_once_it_has_been_over_for_five_minutes(monitor):
    """A check that started at 11:59:59 is saved a moment after twelve."""
    add_check(monitor, ELEVEN + timedelta(minutes=59))
    twelve = ELEVEN + timedelta(hours=1)

    assert retention.roll_up(twelve + timedelta(minutes=4, seconds=59)) == 0
    assert retention.roll_up(twelve + timedelta(minutes=5)) == 1


def test_with_no_checks_there_is_nothing_to_do():
    assert retention.roll_up(NOW) == 0
    assert retention.rolled_up_until() is None


def test_rolling_up_again_changes_nothing(monitor):
    add_check(monitor, ELEVEN)
    retention.roll_up(NOW)

    assert retention.roll_up(NOW) == 0
    assert hours_of(monitor) == [(ELEVEN, 1, 0, {B100: 1})]


def test_the_next_run_carries_on_after_the_last_hour_summed_up(monitor):
    add_check(monitor, ELEVEN)
    retention.roll_up(NOW)
    twelve = ELEVEN + timedelta(hours=1)
    add_check(monitor, twelve + timedelta(minutes=40), ok=False)

    assert retention.rolled_up_until() == twelve
    assert retention.roll_up(NOW + timedelta(hours=1)) == 1

    assert hours_of(monitor) == [(ELEVEN, 1, 0, {B100: 1}), (twelve, 1, 1, {B100: 1})]
    assert retention.rolled_up_until() == twelve + timedelta(hours=1)


def test_two_runs_over_the_same_hours_leave_one_row_each(monitor, monkeypatch):
    """Two workers that both start from the same place: the second one's rows
    replace the first one's instead of failing or doubling the counts."""
    add_check(monitor, ELEVEN)
    add_check(monitor, ELEVEN + timedelta(minutes=1), ok=False)
    retention.roll_up(NOW)
    monkeypatch.setattr(retention, "rolled_up_until", lambda: None)

    assert retention.roll_up(NOW) == 1

    assert hours_of(monitor) == [(ELEVEN, 2, 1, {B100: 2})]


def test_hours_are_cut_in_utc_whatever_zone_the_time_was_given_in(monitor):
    india = timezone.get_fixed_timezone(330)  # UTC+5:30: its hours start at :30 UTC
    add_check(monitor, datetime(2026, 11, 10, 16, 45, tzinfo=india))  # 11:15 UTC

    retention.roll_up(NOW)

    assert hours_of(monitor) == [(ELEVEN, 1, 0, {B100: 1})]
    assert retention.start_of_hour(datetime(2026, 11, 10, 16, 45, tzinfo=india)) == (
        ELEVEN
    )


def test_deleting_a_monitor_deletes_its_hours(monitor):
    add_check(monitor, ELEVEN)
    retention.roll_up(NOW)

    monitor.delete()

    assert not CheckHour.objects.exists()


# --- deleting raw checks ------------------------------------------------------------


def test_checks_past_their_keep_are_deleted_once_their_hour_is_summed_up(monitor):
    old = add_check(monitor, NOW - timedelta(days=8))
    recent = add_check(monitor, NOW - timedelta(days=6))
    retention.roll_up(NOW)

    assert retention.prune(NOW) == 1

    assert list(Check.objects.values_list("pk", flat=True)) == [recent.pk]
    assert CheckHour.objects.filter(
        hour=retention.start_of_hour(old.checked_at)
    ).exists()


def test_the_keep_ends_at_the_top_of_an_hour(monitor):
    """Seven days before 12:30 is 12:30. The checks from 12:00 on are kept, so a
    7-day window never finds part of its first hour gone."""
    week_ago = NOW - timedelta(days=7)
    add_check(monitor, week_ago - timedelta(minutes=31))  # 11:59
    kept = add_check(monitor, week_ago - timedelta(minutes=30))  # 12:00
    retention.roll_up(NOW)

    assert retention.prune(NOW) == 1

    assert list(Check.objects.values_list("pk", flat=True)) == [kept.pk]


def test_nothing_is_deleted_before_anything_is_summed_up(monitor):
    add_check(monitor, NOW - timedelta(days=30))

    assert retention.prune(NOW) == 0
    assert Check.objects.count() == 1


def test_a_check_whose_hour_is_not_summed_up_yet_is_never_deleted(monitor):
    """The roll-up stopped ten days ago. Checks since then are past their keep,
    but they are the only record of their hours."""
    add_check(monitor, NOW - timedelta(days=12))
    retention.roll_up(NOW - timedelta(days=10))
    unsummed = add_check(monitor, NOW - timedelta(days=9))

    assert retention.prune(NOW) == 1

    assert list(Check.objects.values_list("pk", flat=True)) == [unsummed.pk]


def test_the_keep_is_a_setting(monitor, settings):
    settings.PULSE_RAW_CHECK_DAYS = 2
    add_check(monitor, NOW - timedelta(days=3))
    add_check(monitor, NOW - timedelta(days=1))
    retention.roll_up(NOW)

    assert retention.prune(NOW) == 1


# --- deleting old hours -------------------------------------------------------------


def test_hours_older_than_any_window_reads_are_deleted(monitor):
    add_check(monitor, NOW - timedelta(days=91, hours=1))
    kept = add_check(monitor, NOW - timedelta(days=91))
    retention.roll_up(NOW)

    assert retention.forget(NOW) == 1

    assert [hour for hour, *_ in hours_of(monitor)] == [
        retention.start_of_hour(kept.checked_at)
    ]


def test_hours_are_kept_longer_than_the_longest_window_read():
    windows = [*UPTIME_WINDOWS_DAYS, status_page.WINDOW_DAYS]

    assert retention.HOURS_KEPT > timedelta(days=max(windows))


# --- uptime over what is left -------------------------------------------------------


def month(monitor: Monitor, now: datetime = NOW) -> uptime.Totals | None:
    return uptime.totals(Q(monitor=monitor), 30, now).get(monitor.pk)


def test_a_long_window_adds_up_the_same_after_the_raw_checks_are_gone(monitor):
    add_check(monitor, NOW - timedelta(days=20), ok=False)
    add_check(monitor, NOW - timedelta(days=20, minutes=1))
    add_check(monitor, NOW - timedelta(days=9))
    add_check(monitor, NOW - timedelta(days=2), ok=False)
    add_check(monitor, NOW - timedelta(minutes=10))  # its hour is still running
    before = month(monitor)

    retention.roll_up(NOW)
    assert retention.prune(NOW) == 3

    after = month(monitor)
    assert before is not None and after is not None
    assert (before.checks, before.failures) == (5, 2)
    assert (after.checks, after.failures) == (5, 2)


def test_a_check_is_counted_once_whether_summed_up_or_not(monitor):
    """Summed up but not deleted yet: in check_hours and still in checks."""
    add_check(monitor, ELEVEN)
    add_check(monitor, NOW - timedelta(minutes=10))
    retention.roll_up(NOW)

    after = month(monitor)

    assert after is not None
    assert after.checks == 2


def test_checks_older_than_the_window_are_left_out_of_the_sums(monitor):
    add_check(monitor, NOW - timedelta(days=31), ok=False)
    add_check(monitor, NOW - timedelta(days=29))
    retention.roll_up(NOW)

    after = month(monitor)

    assert after is not None
    assert (after.checks, after.failures) == (1, 0)


def test_a_long_window_starts_at_the_top_of_its_first_hour(monitor):
    """Thirty days before 12:30 is 12:30. Its hour is one row from 12:00, so the
    check at 12:10 counts although it is twenty minutes too old."""
    add_check(monitor, NOW - timedelta(days=30, minutes=20))
    add_check(monitor, NOW - timedelta(days=30, minutes=31), ok=False)  # 11:59
    assert month(monitor) is None

    retention.roll_up(NOW)

    after = month(monitor)
    assert after is not None
    assert (after.checks, after.failures) == (1, 0)


def test_monitors_are_kept_apart(monitor):
    other = create_monitor(name="other")
    add_check(monitor, ELEVEN)
    add_check(other, ELEVEN, ok=False)
    add_check(other, NOW - timedelta(minutes=1), ok=False)
    retention.roll_up(NOW)

    both = uptime.totals(Q(monitor__in=[monitor, other]), 30, NOW)

    assert (both[monitor.pk].checks, both[monitor.pk].failures) == (1, 0)
    assert (both[other.pk].checks, both[other.pk].failures) == (2, 2)


def test_a_window_the_raw_checks_cover_is_exact_and_a_longer_one_is_within_5_percent(
    monitor,
):
    for minute in range(19):
        add_check(monitor, ELEVEN + timedelta(minutes=minute), latency_ms=100)
    add_check(monitor, ELEVEN + timedelta(minutes=30), latency_ms=300)
    retention.roll_up(NOW)

    week = uptime.totals(Q(monitor=monitor), 7, NOW)[monitor.pk]
    longer = uptime.totals(Q(monitor=monitor), 30, NOW)[monitor.pk]

    # percentile_cont over the rows themselves: between the 19th and the 20th
    assert week.p95_latency_ms == pytest.approx(110)
    assert longer.p95_latency_ms == latency.value_of(int(B100))  # the 19th of 20
    assert longer.p95_latency_ms == pytest.approx(100, rel=0.05)


def test_one_timeout_in_an_hour_does_not_move_the_p95_of_a_month(monitor):
    """Why hours keep bucket counts and not their own p95. The first hour's p95 is
    about 345 ms (the timeout, among 20 checks) and the second's 100 ms. Their
    average would say 220 ms, but of the 40 checks all but one took 100 ms."""
    for minute in range(19):
        add_check(monitor, ELEVEN - timedelta(minutes=minute + 1), latency_ms=100)
    add_check(monitor, ELEVEN - timedelta(minutes=30), ok=False, latency_ms=5000)
    for minute in range(20):
        add_check(monitor, ELEVEN + timedelta(minutes=minute), latency_ms=100)
    retention.roll_up(NOW)

    after = month(monitor)

    assert after is not None
    assert after.p95_latency_ms == pytest.approx(100, rel=0.05)


def test_latencies_of_checks_not_summed_up_yet_are_counted_in(monitor):
    for minute in range(3):
        add_check(monitor, ELEVEN + timedelta(minutes=minute), latency_ms=100)
    add_check(monitor, NOW - timedelta(minutes=5), latency_ms=500)  # still raw
    retention.roll_up(NOW)

    after = month(monitor)

    assert after is not None
    assert after.p95_latency_ms == pytest.approx(500, rel=0.05)  # the 4th of 4


def test_a_window_no_longer_than_the_keep_never_reads_the_sums(monitor, settings):
    settings.PULSE_RAW_CHECK_DAYS = 30
    add_check(monitor, ELEVEN - timedelta(hours=1), latency_ms=100)
    add_check(monitor, ELEVEN, latency_ms=300)
    retention.roll_up(NOW)

    after = month(monitor)

    assert after is not None
    assert after.p95_latency_ms == 290.0  # exact: percentile_cont over two rows


ages = st.integers(min_value=0, max_value=29 * 24 * 60)  # minutes: inside 30 days


@hypothesis_settings(max_examples=40, deadline=None)
@given(checks=st.lists(st.tuples(ages, st.booleans()), max_size=25))
def test_counts_over_a_month_do_not_change_when_checks_are_rolled_up_and_deleted(
    checks,
):
    """Whatever the checks and wherever they fall: rolling up and deleting moves
    them from one table to the other and loses or doubles none."""
    Monitor.objects.all().delete()  # one database transaction holds every example
    monitor = create_monitor()
    for minutes, ok in checks:
        add_check(monitor, NOW - timedelta(minutes=minutes), ok=ok)
    before = month(monitor)

    retention.roll_up(NOW)
    retention.prune(NOW)

    after = month(monitor)
    if before is None:
        assert after is None
    else:
        assert after is not None
        assert (after.checks, after.failures) == (before.checks, before.failures)


# --- through the API and the task ---------------------------------------------------


def test_the_task_sums_up_then_deletes(monitor):
    now = timezone.now()
    add_check(monitor, now - timedelta(days=8))
    add_check(monitor, now - timedelta(days=1))

    add_check(monitor, now - timedelta(days=100))

    tasks.roll_up_checks()

    assert CheckHour.objects.count() == 2  # the hour from 100 days ago is gone again
    assert Check.objects.count() == 1


def test_uptime_over_30_days_outlives_the_raw_checks(api, monitor):
    now = timezone.now()
    add_check(monitor, now - timedelta(days=20), ok=False, latency_ms=400)
    add_check(monitor, now - timedelta(days=10), latency_ms=200)
    tasks.roll_up_checks()
    assert not Check.objects.exists()

    body = api.get(reverse("monitor-uptime", args=[monitor.pk]), {"days": 30}).json()

    assert body["p95_latency_ms"] == pytest.approx(400, rel=0.05)  # the 2nd of 2
    assert body | {"p95_latency_ms": None} == {
        "days": 30,
        "uptime_percent": 50.0,
        "p95_latency_ms": None,
        "checks": 2,
    }


def test_the_status_page_outlives_the_raw_checks(anon):
    monitor = create_monitor(is_public=True)
    add_check(monitor, timezone.now() - timedelta(days=60), ok=False)
    add_check(monitor, timezone.now() - timedelta(days=30))
    tasks.roll_up_checks()
    assert not Check.objects.exists()

    (entry,) = anon.get("/status").json()["monitors"]

    assert entry["uptime_percent"] == 50.0


def test_uptime_over_30_days_takes_six_queries_however_many_hours(
    api, monitor, django_assert_num_queries
):
    now = timezone.now()
    for day in range(1, 25):
        add_check(monitor, now - timedelta(days=day))
    tasks.roll_up_checks()

    # the token's user, the monitor, how far the roll-up got, the hours' counts,
    # the hours' latency buckets, the checks since
    with django_assert_num_queries(6):
        api.get(reverse("monitor-uptime", args=[monitor.pk]), {"days": 30})


@pytest.mark.parametrize("monitors", [1, 8])
def test_the_status_page_takes_seven_queries_once_hours_are_summed_up(
    django_assert_num_queries, monitors
):
    for n in range(monitors):
        add_check(
            create_monitor(name=f"m{n}", is_public=True),
            timezone.now() - timedelta(days=3),
        )
    tasks.roll_up_checks()

    with django_assert_num_queries(7):
        page = status_page.build_status_page()

    assert len(page["monitors"]) == monitors
