"""The status page for a browser: /status answers 304 while nothing has changed,
and /status/live is the page that keeps asking."""

from datetime import timedelta

import pytest
from django.utils import timezone

from monitors import services, status_page, tasks
from monitors.models import Check, Monitor
from tests.conftest import create_monitor

pytestmark = pytest.mark.django_db


@pytest.fixture
def monitor(db) -> Monitor:
    return create_monitor(is_public=True)


@pytest.fixture
def no_alerts(monkeypatch) -> None:
    monkeypatch.setattr(tasks.send_alert, "apply_async", lambda **kwargs: None)


def test_the_page_comes_with_a_weak_etag_and_must_be_revalidated(anon, monitor):
    response = anon.get("/status")

    assert response.status_code == 200
    assert response.headers["ETag"].startswith('W/"')
    assert response.headers["Cache-Control"] == "no-cache"


def test_asking_again_with_the_etag_gets_304_and_no_body(
    anon, monitor, django_assert_num_queries
):
    first = anon.get("/status")

    with django_assert_num_queries(0):
        again = anon.get("/status", headers={"If-None-Match": first.headers["ETag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == first.headers["ETag"]
    assert again.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("sent", ['W/"something-else"', '"not-even-weak"', "nonsense"])
def test_another_etag_gets_the_page(anon, monitor, sent):
    response = anon.get("/status", headers={"If-None-Match": sent})

    assert response.status_code == 200
    assert response.json()["monitors"][0]["name"] == monitor.name


def test_the_etag_is_found_in_a_list_and_without_its_weak_mark(anon, monitor):
    etag = anon.get("/status").headers["ETag"]

    listed = anon.get("/status", headers={"If-None-Match": f'"other", {etag}'})
    strong = anon.get("/status", headers={"If-None-Match": etag.removeprefix("W/")})

    assert (listed.status_code, strong.status_code) == (304, 304)


def test_a_page_rebuilt_with_nothing_new_is_still_not_modified(anon, monitor, settings):
    """The cache expires every few seconds and the page is built again with a new
    generated_at. A browser that has the old one has lost nothing."""
    settings.PULSE_STATUS_CACHE_SECONDS = 0
    first = anon.get("/status")

    again = anon.get("/status", headers={"If-None-Match": first.headers["ETag"]})

    assert again.status_code == 304


def test_an_incident_changes_the_etag(
    anon, monitor, no_alerts, django_capture_on_commit_callbacks
):
    first = anon.get("/status")
    with django_capture_on_commit_callbacks(execute=True):
        services.open_incident_for(monitor)

    again = anon.get("/status", headers={"If-None-Match": first.headers["ETag"]})

    assert again.status_code == 200
    assert again.headers["ETag"] != first.headers["ETag"]
    assert again.json()["monitors"][0]["state"] == "down"


def test_a_new_check_that_moves_the_numbers_changes_the_etag(anon, monitor, settings):
    settings.PULSE_STATUS_CACHE_SECONDS = 0
    first = anon.get("/status")
    Check.objects.create(
        monitor=monitor,
        ok=False,
        status_code=500,
        latency_ms=40,
        checked_at=timezone.now() - timedelta(minutes=1),
    )

    again = anon.get("/status", headers={"If-None-Match": first.headers["ETag"]})

    assert again.status_code == 200
    assert again.json()["monitors"][0]["uptime_percent"] == 0.0


def test_the_etag_ignores_when_the_page_was_built():
    monitors = [{"id": 1, "name": "shop", "state": "up"}]
    earlier = {"generated_at": "2026-11-15T10:00:00Z", "monitors": monitors}
    later = {"generated_at": "2026-11-15T10:00:15Z", "monitors": monitors}
    other = {"generated_at": "2026-11-15T10:00:00Z", "monitors": []}

    assert status_page.etag_of(earlier) == status_page.etag_of(later)
    assert status_page.etag_of(earlier) != status_page.etag_of(other)


# --- /status/live -------------------------------------------------------------------


def test_the_live_page_is_html_and_needs_no_login(anon):
    response = anon.get("/status/live")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert 'fetch("/t/acme/status"' in response.content.decode()


def test_the_live_page_holds_no_monitor_data(anon):
    """It is the same bytes for everyone: what it shows comes from /status."""
    create_monitor(name="<script>alert(1)</script>", is_public=True)

    html = anon.get("/status/live").content.decode()

    assert "alert(1)" not in html


def test_only_the_pages_own_script_and_style_may_run(anon):
    response = anon.get("/status/live")
    policy = response.headers["Content-Security-Policy"]
    html = response.content.decode()

    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    (nonce,) = {
        part.split("'nonce-")[1].rstrip("'")
        for part in policy.split("; ")
        if "nonce-" in part
    }
    assert html.count(f'nonce="{nonce}"') == 2  # the style and the script


def test_the_nonce_is_new_for_every_response(anon):
    first = anon.get("/status/live").headers["Content-Security-Policy"]
    second = anon.get("/status/live").headers["Content-Security-Policy"]

    assert first != second


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_the_live_page_only_answers_get(anon, method):
    assert getattr(anon, method)("/status/live").status_code == 405
