"""catcher: Pulse's alert inbox. It stands in for Slack or PagerDuty.

    POST /webhook   store a signed alert; 401 if the signature is missing or wrong
    GET  /          everything received, newest first

Pulse signs the exact bytes it sends with HMAC-SHA256 over a shared secret
(PULSE_WEBHOOK_SECRET) and puts the hex digest in X-Pulse-Signature. The catcher
recomputes it over the raw body it received, so any change in transit, or a sender
without the secret, is rejected before anything is stored.
"""

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request

SECRET = os.environ["PULSE_WEBHOOK_SECRET"].encode()
SIGNATURE_HEADER = "X-Pulse-Signature"

app = FastAPI(title="catcher")
received: list[dict[str, Any]] = []


def signature_of(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, int]:
    body = (
        await request.body()
    )  # the raw bytes: what was signed, not a re-serialisation
    expected = signature_of(body)
    given = request.headers.get(SIGNATURE_HEADER, "")
    # compare_digest takes the same time whether the first or last byte differs,
    # so an attacker can't guess the signature one character at a time.
    if not hmac.compare_digest(expected, given):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload: Any = json.loads(body)
    except ValueError:
        payload = body.decode(errors="replace")
    received.insert(
        0,
        {
            "received_at": datetime.now(UTC).isoformat(),
            "headers": dict(request.headers),
            "body": payload,
        },
    )
    return {"stored": len(received)}


@app.get("/")
def index() -> list[dict[str, Any]]:
    return received
