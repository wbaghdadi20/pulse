"""Checks must not reach addresses that only Pulse can reach (SSRF).

Three layers, tested in this order: which addresses are forbidden, which URLs
that makes unsafe, and the two places the rule is applied: when a monitor is
created or edited, and again on every check.
"""

import ipaddress
import socket

import httpx
import pytest
from django.urls import reverse
from hypothesis import given
from hypothesis import strategies as st

from monitors import clients, services, ssrf
from monitors.checks import probe
from monitors.models import Check, Monitor
from tests.conftest import MONITOR_PAYLOAD, FakeDNS

DOCKER = "172.18.0.3"  # what a Compose service name resolves to
METADATA = "169.254.169.254"  # hands out the server's credentials on AWS and GCP

FORBIDDEN = [
    "127.0.0.1",
    "127.8.9.10",
    "10.0.0.5",
    DOCKER,
    "192.168.1.1",
    METADATA,
    "100.64.0.1",
    "100.100.100.200",
    "0.0.0.0",
    "192.0.2.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "::1",
    "::",
    "::ffff:127.0.0.1",
    f"::ffff:{METADATA}",
    "64:ff9b::7f00:1",
    "fe80::1",
    "fd00::1",
    "ff02::1",
]
PUBLIC = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700::1111"]

# --- which addresses ------------------------------------------------------------


@pytest.mark.parametrize("address", FORBIDDEN)
def test_addresses_off_the_public_internet_are_forbidden(address):
    assert ssrf.is_forbidden(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", PUBLIC)
def test_public_addresses_are_not(address):
    assert not ssrf.is_forbidden(ipaddress.ip_address(address))


def test_the_usual_five_properties_miss_shared_address_space():
    """Why is_forbidden also asks is_global: this is a real metadata address."""
    ip = ipaddress.ip_address("100.100.100.200")

    usual_five = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )

    assert not usual_five
    assert ssrf.is_forbidden(ip)


@pytest.mark.parametrize("address", ["224.0.0.1", "64:ff9b::7f00:1"])
def test_is_global_alone_would_let_multicast_and_nat64_through(address):
    """And why the five stay: these count as global."""
    ip = ipaddress.ip_address(address)

    assert ip.is_global
    assert ssrf.is_forbidden(ip)


@given(
    st.sampled_from(
        ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]
        + ["169.254.0.0/16", "100.64.0.0/10", "fc00::/7", "fe80::/10"]
    ).flatmap(lambda network: st.ip_addresses(network=network))
)
def test_every_address_in_a_private_range_is_forbidden(ip):
    assert ssrf.is_forbidden(ip)


# --- which URLs -----------------------------------------------------------------


def test_a_host_on_a_public_address_is_safe_and_that_address_is_returned(dns):
    dns["example.com"] = ["93.184.216.34"]

    assert ssrf.safe_address("https://example.com/health") == "93.184.216.34"


@pytest.mark.parametrize("address", FORBIDDEN)
def test_a_name_pointing_at_a_forbidden_address_is_blocked(dns, address):
    """The name looks harmless; only the address it resolves to gives it away."""
    dns["innocent.example.com"] = [address]

    with pytest.raises(ssrf.BlockedAddress):
        ssrf.safe_address("http://innocent.example.com/")


def test_one_forbidden_address_among_several_blocks_the_host(dns):
    dns["mixed.example.com"] = ["93.184.216.34", "127.0.0.1"]

    with pytest.raises(ssrf.BlockedAddress):
        ssrf.safe_address("http://mixed.example.com/")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0x7f.0.0.1/",
        "http://2130706433/",
        "http://017700000001/",
        "http://localhost/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        f"http://{METADATA}/latest/meta-data/",
    ],
)
def test_every_spelling_of_a_forbidden_address_is_blocked(
    dns: FakeDNS, monkeypatch, url
):
    """With the real resolver: it is what turns 127.1 and 2130706433 into 127.0.0.1.
    None of these needs the network."""
    monkeypatch.setattr(ssrf, "resolve", dns.real)

    with pytest.raises(ssrf.BlockedAddress):
        ssrf.safe_address(url)


def test_a_name_that_does_not_resolve_is_not_blocked_it_just_fails(dns):
    dns["not-registered-yet.example"] = []

    with pytest.raises(socket.gaierror):
        ssrf.safe_address("http://not-registered-yet.example/")


@pytest.mark.parametrize("host", ["a..b", "a" * 300 + ".com"])
def test_a_name_that_cannot_exist_counts_as_not_resolving(dns, monkeypatch, host):
    monkeypatch.setattr(ssrf, "resolve", dns.real)

    with pytest.raises(socket.gaierror):
        ssrf.safe_address(f"http://{host}/")


# --- our own targets ------------------------------------------------------------


@pytest.fixture
def allow_flaky(settings, dns) -> None:
    settings.PULSE_ALLOWED_PRIVATE_HOSTS = ["flaky", "shop"]
    for host in (
        "flaky",
        "flaky.",
        "shop",
        "postgres",
        "flaky.evil.example",
        "notflaky",
    ):
        dns[host] = [DOCKER]


@pytest.mark.parametrize(
    "url", ["http://flaky:8000/health", "http://shop:8000/ht/", "http://FLAKY./"]
)
def test_an_allow_listed_host_may_be_on_a_private_address(allow_flaky, url):
    assert ssrf.safe_address(url) == DOCKER


@pytest.mark.parametrize(
    "url",
    ["http://postgres:5432/", "http://flaky.evil.example/", "http://notflaky/"],
    ids=["another private host", "the name as a subdomain", "the name as a suffix"],
)
def test_the_allow_list_matches_whole_hostnames_only(allow_flaky, url):
    with pytest.raises(ssrf.BlockedAddress):
        ssrf.safe_address(url)


def test_with_an_empty_allow_list_no_private_host_is_allowed(settings, dns):
    settings.PULSE_ALLOWED_PRIVATE_HOSTS = []
    dns["flaky"] = [DOCKER]

    with pytest.raises(ssrf.BlockedAddress):
        ssrf.safe_address("http://flaky:8000/health")


# --- when a monitor is created or edited -------------------------------------------

pytest_db = pytest.mark.django_db


def create(api, url: str):
    return api.post(reverse("monitor-list"), {**MONITOR_PAYLOAD, "url": url}, "json")


@pytest_db
@pytest.mark.parametrize(
    "url, resolves_to",
    [
        ("http://postgres:5432/", DOCKER),
        ("http://redis:6379/", DOCKER),
        (f"http://{METADATA}/", METADATA),
        ("http://127.0.0.1/", "127.0.0.1"),
        ("http://localhost:8000/health", "127.0.0.1"),
        ("http://rebind.attacker.example/", "10.0.0.5"),
    ],
)
def test_a_monitor_for_an_internal_address_is_rejected(api, dns, url, resolves_to):
    dns[httpx.URL(url).host] = [resolves_to]

    response = create(api, url)

    assert response.status_code == 400
    assert set(response.json()) == {"url"}
    assert resolves_to not in response.content.decode() or resolves_to in url
    assert not Monitor.objects.exists()


@pytest_db
@pytest.mark.parametrize("url", ["http://flaky:8000/health", "http://shop:8000/ht/"])
def test_a_monitor_for_an_allow_listed_target_is_created(api, allow_flaky, url):
    assert create(api, url).status_code == 201


@pytest_db
def test_a_monitor_cannot_be_edited_to_point_at_an_internal_address(api, monitor, dns):
    dns["postgres"] = [DOCKER]

    response = api.patch(
        reverse("monitor-detail", args=[monitor.pk]),
        {"url": "http://postgres:5432/"},
        format="json",
    )

    assert response.status_code == 400
    monitor.refresh_from_db()
    assert monitor.url == MONITOR_PAYLOAD["url"]


@pytest_db
def test_a_monitor_for_a_name_that_does_not_resolve_yet_is_created(api, dns):
    dns["launching-soon.example"] = []

    assert create(api, "http://launching-soon.example/").status_code == 201


@pytest_db
@pytest.mark.parametrize(
    "url",
    ["http://[not-an-address]/", "http://example.com/\x7f"],
    ids=["brackets without an IPv6 address", "a control character"],
)
def test_a_url_that_cannot_be_parsed_is_a_400(api, url):
    response = create(api, url)

    assert response.status_code == 400
    assert set(response.json()) == {"url"}


# --- on every check -------------------------------------------------------------


class Target:
    """An httpx transport that records what it was sent and answers 200."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or httpx.Response(200)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    @property
    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def monitor_for(url: str) -> Monitor:
    return Monitor(**{**MONITOR_PAYLOAD, "url": url})


def test_a_host_that_now_resolves_to_an_internal_address_is_not_called(dns):
    """It was public when the monitor was created. DNS changed since."""
    dns["rebind.attacker.example"] = ["127.0.0.1"]
    target = Target()

    result = probe(monitor_for("http://rebind.attacker.example/"), target.client)

    assert result == {
        "ok": False,
        "status_code": None,
        "latency_ms": result["latency_ms"],
        "error_type": "blocked",
    }
    assert target.requests == []


def test_the_request_goes_to_the_address_that_was_judged(dns):
    dns["example.com"] = ["93.184.216.34"]
    target = Target()

    probe(monitor_for("http://example.com:8080/health?deep=1"), target.client)

    (request,) = target.requests
    assert request.url == "http://93.184.216.34:8080/health?deep=1"
    assert request.headers["host"] == "example.com:8080"


def test_https_still_verifies_the_certificate_against_the_name(dns):
    dns["example.com"] = ["93.184.216.34"]
    target = Target()

    probe(monitor_for("https://example.com/"), target.client)

    (request,) = target.requests
    assert request.url.host == "93.184.216.34"
    assert request.extensions["sni_hostname"] == "example.com"


def test_the_name_is_looked_up_once_per_check_so_the_answer_cannot_change(dns):
    """DNS rebinding: public for the lookup that is judged, internal for the next.
    There is no next: the connection uses the judged answer."""
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    lookups = []

    def rebinding(host: str, port: int) -> list[str]:
        lookups.append(host)
        return next(answers)

    target = Target()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ssrf, "resolve", rebinding)
        result = probe(monitor_for("http://rebind.attacker.example/"), target.client)

    assert result["ok"] is True
    assert lookups == ["rebind.attacker.example"]
    assert [request.url.host for request in target.requests] == ["93.184.216.34"]


def test_a_redirect_to_an_internal_address_is_not_followed():
    redirect = httpx.Response(302, headers={"Location": "http://127.0.0.1:6379/"})
    target = Target(redirect)

    result = probe(monitor_for("http://example.com/"), target.client)

    assert (result["status_code"], result["error_type"]) == (302, "wrong_status")
    assert len(target.requests) == 1


def test_the_shared_probe_client_never_follows_redirects_or_uses_a_proxy():
    client = clients.probe_client()

    assert client.follow_redirects is False
    assert client.trust_env is False


@pytest_db
def test_a_blocked_check_is_recorded_like_any_other_failure(monitor, dns):
    dns["target"] = [DOCKER]

    result = services.check_monitor(monitor)

    assert result["error_type"] == "blocked"
    assert Check.objects.get().error_type == Check.ErrorType.BLOCKED


@pytest_db
def test_an_allow_listed_target_is_checked(monitor, allow_flaky, monkeypatch):
    Monitor.objects.filter(pk=monitor.pk).update(url="http://flaky:8000/health")
    monitor.refresh_from_db()
    target = Target()
    monkeypatch.setattr(clients, "probe_client", lambda: target.client)

    result = services.check_monitor(monitor)

    assert result["ok"] is True
    assert target.requests[0].url == f"http://{DOCKER}:8000/health"
