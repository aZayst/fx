"""Fake counterparties: receive Trade Capture Reports and (maybe) send acks back.

Each counterparty has a mode you can flip at runtime:
    ACK     accept every report
    REJECT  reject every report
    SILENT  swallow the report and never answer  -> the gateway sees an unacked break
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from ..config import Settings
from ..schemas import SetModeIn

log = logging.getLogger("fxlab.counterparty")

AckPoster = Callable[[dict[str, Any]], None]


def http_ack_poster(settings: Settings) -> AckPoster:
    headers = {"X-API-Key": settings.api_key} if settings.api_key else {}

    def post(ack: dict[str, Any]) -> None:
        httpx.post(
            settings.gateway_ack_url, json=ack, headers=headers, timeout=5
        ).raise_for_status()

    return post


def create_counterparty_app(
    settings: Settings | None = None,
    ack_poster: AckPoster | None = None,
    ack_delay_seconds: float = 0.5,
) -> FastAPI:
    settings = settings or Settings.from_env()
    post_ack = ack_poster or http_ack_poster(settings)
    modes = {code: "ACK" for code in settings.counterparties}
    app = FastAPI(title="fxlab counterparties", version="0.1.0")

    def respond(code: str, report_id: str) -> None:
        time.sleep(ack_delay_seconds)  # simulate the counterparty's processing time
        mode = modes[code]
        if mode == "SILENT":
            log.info("%s stays silent for %s", code, report_id)
            return
        ack: dict[str, Any] = {
            "report_id": report_id,
            "status": "ACCEPTED" if mode == "ACK" else "REJECTED",
            "reject_reason": None if mode == "ACK" else f"{code} declined (mode=REJECT)",
        }
        try:
            post_ack(ack)
        except Exception as exc:
            log.warning("could not deliver ack for %s: %s", report_id, exc)

    @app.post("/tcr/{code}", status_code=202)
    def receive_tcr(code: str, tcr: dict[str, Any], background: BackgroundTasks) -> dict[str, str]:
        if code not in modes:
            raise HTTPException(404, f"unknown counterparty {code}")
        report_id = str(tcr.get("report_id", ""))
        if not report_id:
            raise HTTPException(422, "report_id is required")
        background.add_task(respond, code, report_id)  # ack comes back asynchronously
        return {"received": report_id}

    @app.get("/agencies")
    def agencies() -> list[dict[str, str]]:
        return [{"code": c, "mode": m} for c, m in modes.items()]

    @app.put("/agencies/{code}/mode")
    def set_mode(code: str, body: SetModeIn) -> dict[str, str]:
        if code not in modes:
            raise HTTPException(404, f"unknown counterparty {code}")
        modes[code] = body.mode
        return {"code": code, "mode": body.mode}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_counterparty_app()
