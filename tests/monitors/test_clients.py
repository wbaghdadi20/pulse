"""The two shared httpx clients, against a real socket on loopback.

What matters here is how many TCP connections each client opens, and a mocked
transport has no connections to count. `server` is a throwaway HTTP/1.1 server in
a thread that counts every connection it accepts.
"""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from monitors import clients


class CountingServer(ThreadingHTTPServer):
    connections = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keeps the connection open if the client wants to

    def setup(self) -> None:
        self.server.connections += 1  # type: ignore[attr-defined]
        super().setup()

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[CountingServer]:
    with CountingServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield httpd
        httpd.shutdown()
        thread.join()


def test_each_client_is_built_once_per_process():
    assert clients.probe_client() is clients.probe_client()
    assert clients.webhook_client() is clients.webhook_client()
    assert clients.probe_client() is not clients.webhook_client()


def test_the_probe_client_opens_a_new_connection_for_every_check(server):
    for _ in range(3):
        clients.probe_client().get(server.url)

    assert server.connections == 3


def test_the_webhook_client_reuses_its_connection(server):
    for _ in range(3):
        clients.webhook_client().get(server.url)

    assert server.connections == 1


def test_the_webhook_client_takes_its_timeout_from_settings(settings):
    settings.PULSE_WEBHOOK_TIMEOUT_S = 9
    clients.webhook_client.cache_clear()
    try:
        timeout = clients.webhook_client().timeout
    finally:
        clients.webhook_client.cache_clear()

    assert timeout.connect == timeout.read == 9
