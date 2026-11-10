"""Run inside the catcher container: docker compose run --rm catcher pytest -q"""

import hashlib
import hmac
import json
import os

import pytest

# Under `docker compose run` the real secret is already set; set one only if not.
SECRET = os.environ.setdefault("PULSE_WEBHOOK_SECRET", "test-secret").encode()

from fastapi.testclient import TestClient  # noqa: E402  (needs the env var above)
from main import SIGNATURE_HEADER, app, received  # noqa: E402

client = TestClient(app)
BODY = json.dumps({"event": "incident.opened", "monitor": "flaky"}).encode()


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def empty_inbox():
    received.clear()


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
