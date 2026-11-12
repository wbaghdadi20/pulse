"""catcher: Pulse's alert inbox. It stands in for Slack or PagerDuty.

    POST /webhook   store a signed alert; 401 if the signature is missing or wrong;
                    an alert_id already stored is answered 200 and not stored again
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
seen: set[int] = set()


def signature_of(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, int | bool]:
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
    alert_id = payload.get("alert_id") if isinstance(payload, dict) else None
    if isinstance(alert_id, int):
        if alert_id in seen:
            # 200, not 409: any other answer would make Pulse retry again.
            return {"stored": len(received), "duplicate": True}
        seen.add(alert_id)
    received.insert(
        0,
        {
            "received_at": datetime.now(UTC).isoformat(),
            "headers": dict(request.headers),
            "body": payload,
        },
    )
    return {"stored": len(received), "duplicate": False}


@app.get("/")
def index() -> list[dict[str, Any]]:
    return received
