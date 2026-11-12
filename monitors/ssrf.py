"""Keeps checks away from addresses that only Pulse can reach.

A monitor's URL comes from a user and Pulse calls it from inside its own network,
so the host is resolved here and judged by its addresses, never by how the URL
is spelled. Why, and what is left open: docs/adr/0015-ssrf-guard.md
"""

import ipaddress
import socket

import httpx
from django.conf import settings

DEFAULT_PORTS = {"http": 80, "https": 443}


class BlockedAddress(Exception):
    """The host resolves to an address that checks may not call."""


def safe_address(url: str) -> str:
    """The one address a check of this URL may connect to.

    Raises BlockedAddress if any address the host resolves to is forbidden, unless
    the host is in PULSE_ALLOWED_PRIVATE_HOSTS. Raises socket.gaierror if the host
    does not resolve, and httpx.InvalidURL if the URL cannot be parsed.

    The caller connects to the returned address instead of the name: a second
    lookup could get a different answer than the one judged here.
    """
    # Parsed by httpx, as the request will be: two parsers can disagree on the host.
    target = httpx.URL(url)
    addresses = resolve(target.host, target.port or DEFAULT_PORTS[target.scheme])
    if not is_allowed_private_host(target.host) and any(
        is_forbidden(ipaddress.ip_address(address)) for address in addresses
    ):
        raise BlockedAddress(target.host)
    return addresses[0]


def is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for every address that is not a host on the public internet.

    The first five are the usual list. They miss 100.64.0.0/10 (shared address
    space, where one cloud keeps its metadata service), which `not is_global`
    catches. `is_global` alone would let multicast and NAT64 through.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or not ip.is_global
    )


def is_allowed_private_host(host: str) -> bool:
    """Our own targets live on private addresses. Exact names, from configuration only."""
    allowed = {name.lower() for name in settings.PULSE_ALLOWED_PRIVATE_HOSTS}
    return host.lower().rstrip(".") in allowed


def resolve(host: str, port: int) -> list[str]:
    """Every address the host has, in the resolver's order. Also parses IP literals
    in any spelling (127.1, 0x7f.0.0.1, 2130706433), which is why nothing else does."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except UnicodeError as exc:
        # A label that is empty or too long: no such name can exist.
        raise socket.gaierror(socket.EAI_NONAME, str(exc)) from exc
    return list(dict.fromkeys(str(info[4][0]) for info in found))
