"""The load test: what fifty people using Pulse at once ask of it. See LOADTEST.md.

From the repo root:
    locust -f loadtest/locustfile.py --headless -u 50 -r 5 -t 2m --host http://localhost:8100
"""

import itertools
import random

from locust import FastHttpUser, constant, task

ACCOUNTS = 50
PASSWORD = "loadtest-only-not-a-secret"  # the one loadtest/seed.py gave every account

numbers = itertools.count()


class PulseUser(FastHttpUser):
    # No pause between requests: the next one is sent when the last one is answered.
    # Fifty users are then fifty requests in flight, and requests per second is what
    # Pulse can serve rather than how fast the users choose to click.
    wait_time = constant(0)

    def on_start(self) -> None:
        number = next(numbers) % ACCOUNTS
        # Each user is its own client address to the rate limiter (PULSE_NUM_PROXIES=1).
        self.visitor = {"X-Forwarded-For": f"10.0.0.{number + 1}"}
        login = self.client.post(
            "/login",
            json={
                "email": f"loadtest-{number:02}@example.com",
                "password": PASSWORD,
            },
            headers=self.visitor,
        )
        token = login.json()["access_token"]
        self.headers = {**self.visitor, "Authorization": f"Bearer {token}"}
        monitors = self.client.get("/monitors/", headers=self.headers).json()
        self.monitor_ids = [monitor["id"] for monitor in monitors["results"]]

    @task(70)
    def status_page(self) -> None:
        # The status page is public: asked for without a token, as a visitor would.
        self.client.get("/status", headers=self.visitor)

    @task(20)
    def list_monitors(self) -> None:
        self.client.get("/monitors/", headers=self.headers)

    @task(10)
    def uptime(self) -> None:
        monitor_id = random.choice(self.monitor_ids)
        self.client.get(
            f"/monitors/{monitor_id}/uptime/?days=30",
            headers=self.headers,
            name="/monitors/[id]/uptime/?days=30",
        )
