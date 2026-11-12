"""Run inside the catcher container: docker compose run --rm catcher pytest -q"""

import hashlib
import hmac
import json
import os

import pytest

# Under `docker compose run` the real secret is already set; set one only if not.
SECRET = os.environ.setdefault("PULSE_WEBHOOK_SECRET", "test-secret").encode()

from fastapi.testclient import TestClient  # noqa: E402  (needs the env var above)
from main import SIGNATURE_HEADER, app, received, seen  # noqa: E402

client = TestClient(app)
BODY = json.dumps({"event": "incident.opened", "monitor": "flaky"}).encode()


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def empty_inbox():
    received.clear()
    seen.clear()


def deliver(payload: dict[str, object]):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body, headers={SIGNATURE_HEADER: sign(body)})


def test_a_correctly_signed_alert_is_stored():
    response = client.post(
        "/webhook", content=BODY, headers={SIGNATURE_HEADER: sign(BODY)}
    )

    assert response.status_code == 200
    assert client.get("/").json()[0]["body"] == json.loads(BODY)


def test_a_wrong_signature_is_rejected_with_401_and_not_stored():
    wrong = sign(BODY, secret=b"someone-else")
    response = client.post("/webhook", content=BODY, headers={SIGNATURE_HEADER: wrong})

    assert response.status_code == 401
    assert client.get("/").json() == []


def test_a_missing_signature_is_rejected():
    assert client.post("/webhook", content=BODY).status_code == 401


def test_a_body_changed_after_signing_is_rejected():
    tampered = BODY.replace(b"flaky", b"shop")
    response = client.post(
        "/webhook", content=tampered, headers={SIGNATURE_HEADER: sign(BODY)}
    )

    assert response.status_code == 401


def test_newest_alert_comes_first():
    for i in (1, 2):
        body = json.dumps({"n": i}).encode()
        client.post("/webhook", content=body, headers={SIGNATURE_HEADER: sign(body)})

    assert [item["body"]["n"] for item in client.get("/").json()] == [2, 1]


def test_an_alert_delivered_twice_is_stored_once_and_answered_200_both_times():
    first = deliver({"alert_id": 42, "event": "incident.opened"})
    second = deliver({"alert_id": 42, "event": "incident.opened"})

    assert first.status_code == second.status_code == 200
    assert first.json() == {"stored": 1, "duplicate": False}
    assert second.json() == {"stored": 1, "duplicate": True}
    assert len(client.get("/").json()) == 1


def test_alerts_with_different_ids_are_both_stored():
    deliver({"alert_id": 42, "event": "incident.opened"})
    deliver({"alert_id": 43, "event": "incident.resolved"})

    assert len(client.get("/").json()) == 2


def test_a_body_without_an_alert_id_is_stored_every_time():
    deliver({"event": "incident.opened"})
    deliver({"event": "incident.opened"})

    assert len(client.get("/").json()) == 2


def test_a_rejected_delivery_does_not_use_up_its_alert_id():
    body = json.dumps({"alert_id": 42}).encode()
    wrong = sign(body, secret=b"someone-else")
    client.post("/webhook", content=body, headers={SIGNATURE_HEADER: wrong})

    assert deliver({"alert_id": 42}).json() == {"stored": 1, "duplicate": False}
