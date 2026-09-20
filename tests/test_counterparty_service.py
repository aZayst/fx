from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from fxlab.config import Settings
from fxlab.counterparty.app import create_counterparty_app

TCR = {"report_id": "TRD-1-CCP_ALPHA-1", "symbol": "EUR/USD"}


def make(acks: list[dict[str, Any]]) -> TestClient:
    app = create_counterparty_app(Settings(), ack_poster=acks.append, ack_delay_seconds=0)
    return TestClient(app)


def test_ack_mode_accepts() -> None:
    acks: list[dict[str, Any]] = []
    assert make(acks).post("/tcr/CCP_ALPHA", json=TCR).status_code == 202
    assert acks == [{"report_id": "TRD-1-CCP_ALPHA-1", "status": "ACCEPTED", "reject_reason": None}]


def test_reject_mode_rejects_with_reason() -> None:
    acks: list[dict[str, Any]] = []
    c = make(acks)
    c.put("/agencies/CCP_ALPHA/mode", json={"mode": "REJECT"})
    c.post("/tcr/CCP_ALPHA", json=TCR)
    assert acks[0]["status"] == "REJECTED" and "CCP_ALPHA" in acks[0]["reject_reason"]


def test_silent_mode_never_answers() -> None:
    acks: list[dict[str, Any]] = []
    c = make(acks)
    c.put("/agencies/CCP_ALPHA/mode", json={"mode": "SILENT"})
    assert c.post("/tcr/CCP_ALPHA", json=TCR).status_code == 202
    assert acks == []


def test_modes_are_per_counterparty_and_validated() -> None:
    c = make([])
    c.put("/agencies/CCP_BRAVO/mode", json={"mode": "REJECT"})
    modes = {a["code"]: a["mode"] for a in c.get("/agencies").json()}
    assert modes == {"CCP_ALPHA": "ACK", "CCP_BRAVO": "REJECT", "CCP_CHARLIE": "ACK"}
    assert c.put("/agencies/CCP_BRAVO/mode", json={"mode": "MAYBE"}).status_code == 422
    assert c.post("/tcr/NOPE", json=TCR).status_code == 404
