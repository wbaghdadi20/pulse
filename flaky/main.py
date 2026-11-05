"""flaky: a target you control, for making Pulse see outages on demand.

    POST /mode  {"mode": "ok" | "500" | "slow"}   set how GET / behaves
    GET  /mode                                    read the current mode
    GET  /                                        answer according to the mode

"down" is not a mode: stop the container (`docker compose stop flaky`) so Pulse
sees a real connection failure, like a crashed service would cause.
"""

import asyncio
from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel

Mode = Literal["ok", "500", "slow"]
SLOW_SECONDS = 10

app = FastAPI(title="flaky")
state = {"mode": "ok"}


class ModeIn(BaseModel):
    mode: Mode


@app.get("/mode")
def get_mode() -> dict:
    return {"mode": state["mode"]}


@app.post("/mode")
def set_mode(body: ModeIn) -> dict:
    state["mode"] = body.mode
    return {"mode": state["mode"]}


@app.get("/")
async def root(response: Response) -> dict:
    mode = state["mode"]
    if mode == "500":
        response.status_code = 500
        return {"mode": mode, "detail": "pretending to be broken"}
    if mode == "slow":
        await asyncio.sleep(SLOW_SECONDS)
    return {"mode": mode}
