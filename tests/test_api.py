"""End-to-end through the HTTP API (order -> booking -> post-trade -> monitoring)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from fxlab.config import Settings
from fxlab.main import create_app
from fxlab.pricing import PriceFeed

from .fakes import FakeClient

BUY = {"account_id": "FUND-1", "symbol": "EUR/USD", "side": "BUY", "quantity": "1000000"}


def ack_all(client: TestClient, fake: FakeClient, status: str = "ACCEPTED") -> None:
    for _, tcr in fake.sent:
        resp = client.post(
            "/api/posttrade/acks", json={"report_id": tcr["report_id"], "status": status}
        )
        assert resp.status_code == 200


def test_order_books_trade_and_sends_three_tcrs(
    client: TestClient, fake_client: FakeClient
) -> None:
    resp = client.post("/api/orders", json=BUY)
    assert resp.status_code == 201 and resp.json()["status"] == "FILLED"

    [trade] = client.get("/api/trades").json()
    assert trade["status"] == "BOOKED" and trade["trade_ref"].startswith("TRD-")
    assert len(fake_client.sent) == 3  # background dispatch has run


def test_full_lifecycle_to_settlement(client: TestClient, fake_client: FakeClient) -> None:
    client.post("/api/orders", json=BUY)
    ack_all(client, fake_client)

    [trade] = client.get("/api/trades").json()
    assert trade["status"] == "CONFIRMED"
    detail = client.get(f"/api/trades/{trade['id']}").json()
    assert {c["status"] for c in detail["confirmations"]} == {"ACKED"}

    settled = client.post("/api/admin/settle", params={"as_of": trade["value_date"]}).json()
    assert settled["settled"] == [trade["trade_ref"]]
    assert client.get(f"/api/trades/{trade['id']}").json()["status"] == "SETTLED"


def test_break_is_visible_and_resend_fixes_it(client: TestClient, fake_client: FakeClient) -> None:
    client.post("/api/orders", json=BUY)
    tcrs = dict((cp, t["report_id"]) for cp, t in fake_client.sent)
    for cp, status in (("CCP_ALPHA", "ACCEPTED"), ("CCP_BRAVO", "REJECTED"), ("CCP_CHARLIE", None)):
        if status:
            client.post("/api/posttrade/acks", json={"report_id": tcrs[cp], "status": status})

    breaks = client.get("/api/monitoring/reconciliation", params={"breaks_only": True}).json()
    assert {(b["counterparty"], b["status"]) for b in breaks} == {
        ("CCP_BRAVO", "REJECTED"),
        ("CCP_CHARLIE", "SENT"),
    }

    trade_id = client.get("/api/trades").json()[0]["id"]
    resend = client.post("/api/posttrade/resend", json={"trade_id": trade_id}).json()
    assert resend["resent_to"] == ["CCP_BRAVO", "CCP_CHARLIE"]

    ack_all_new = [t for cp, t in fake_client.sent if t["report_id"].endswith("-2")]
    assert len(ack_all_new) == 2
    for tcr in ack_all_new:
        client.post(
            "/api/posttrade/acks", json={"report_id": tcr["report_id"], "status": "ACCEPTED"}
        )
    assert client.get("/api/monitoring/reconciliation", params={"breaks_only": True}).json() == []
    assert client.get("/api/trades").json()[0]["status"] == "CONFIRMED"


def test_resend_validates_input(client: TestClient) -> None:
    assert client.post("/api/posttrade/resend", json={"trade_id": 999}).status_code == 404
    client.post("/api/orders", json=BUY)
    bad = client.post("/api/posttrade/resend", json={"trade_id": 1, "counterparty": "NOPE"})
    assert bad.status_code == 422


def test_rejected_order_is_recorded_not_errored(client: TestClient) -> None:
    resp = client.post("/api/orders", json={**BUY, "quantity": "5"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "REJECTED"
    assert client.get("/api/trades").json() == []


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"account_id": "NOPE"}, 404),
        ({"symbol": "XXX/YYY"}, 404),
        ({"quantity": "-1"}, 422),
        ({"side": "HOLD"}, 422),
        ({"order_type": "LIMIT"}, 422),  # limit price missing
    ],
)
def test_order_validation_errors(client: TestClient, patch: dict[str, Any], code: int) -> None:
    assert client.post("/api/orders", json={**BUY, **patch}).status_code == code


def test_limit_order_lifecycle_via_api(client: TestClient) -> None:
    limit = {**BUY, "order_type": "LIMIT", "limit_price": "1.0000"}
    order = client.post("/api/orders", json=limit).json()
    assert order["status"] == "OPEN"
    assert client.get("/api/orders", params={"status": "OPEN"}).json()[0]["id"] == order["id"]
    assert client.delete(f"/api/orders/{order['id']}").json()["status"] == "CANCELLED"
    assert client.delete(f"/api/orders/{order['id']}").status_code == 409
    assert client.delete("/api/orders/9999").status_code == 404


def test_positions_and_quotes(client: TestClient) -> None:
    client.post("/api/orders", json=BUY)
    [pos] = client.get("/api/positions", params={"account_id": "FUND-1"}).json()
    assert pos["symbol"] == "EUR/USD" and float(pos["net_base"]) == 1_000_000
    assert client.get("/api/quotes/EUR/USD").json()["symbol"] == "EUR/USD"
    assert client.get("/api/quotes/NOPE").status_code == 404
    assert len(client.get("/api/quotes").json()) == 5


def test_injected_bad_trade_raises_off_market_alert(client: TestClient) -> None:
    client.post(
        "/api/admin/trades/inject",
        json={**BUY, "price": "1.2000"},
    )
    [alert] = client.get("/api/alerts").json()
    assert alert["rule"] == "OFF_MARKET_PRICE" and alert["status"] == "OPEN"
    acked = client.post(f"/api/alerts/{alert['id']}/ack").json()
    assert acked["status"] == "ACKNOWLEDGED"
    assert client.get("/api/monitoring/summary").json()["open_alerts"] == 0


def test_admin_counterparty_modes_and_prices(client: TestClient, fake_client: FakeClient) -> None:
    resp = client.put("/api/admin/counterparties/CCP_BRAVO/mode", json={"mode": "SILENT"})
    assert resp.status_code == 200 and fake_client.modes["CCP_BRAVO"] == "SILENT"
    assert client.get("/api/admin/counterparties").json()["CCP_BRAVO"] == "SILENT"
    assert (
        client.put("/api/admin/counterparties/NOPE/mode", json={"mode": "ACK"}).status_code == 404
    )
    quote = client.post("/api/admin/prices/EUR/USD", json={"mid": "1.2"}).json()
    assert quote["mid"].startswith("1.2")


def test_accounts_are_seeded(client: TestClient) -> None:
    assert [a["id"] for a in client.get("/api/accounts").json()] == ["CORP-2", "FUND-1", "HEDGE-3"]


def test_system_endpoints(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    metrics = client.get("/metrics").text
    assert "fxlab_orders_total" in metrics
    assert client.get("/").status_code == 200


def test_api_key_protects_only_the_ack_callback(fake_client: FakeClient) -> None:
    """The secret exists so nobody can forge acks; the dashboard/admin controls need no key."""
    secured = Settings(database_url="sqlite://", background_tasks=False, api_key="s3cret")
    with TestClient(create_app(secured, fake_client, PriceFeed(seed=1))) as c:
        ack = {"report_id": "x", "status": "ACCEPTED"}
        assert c.post("/api/posttrade/acks", json=ack).status_code == 401
        assert (
            c.post("/api/posttrade/acks", json=ack, headers={"X-API-Key": "bad"}).status_code == 401
        )
        assert (
            c.post("/api/posttrade/acks", json=ack, headers={"X-API-Key": "s3cret"}).status_code
            == 200
        )
        # everything a person uses in the browser works without any key
        assert c.get("/api/admin/counterparties").status_code == 200
        mode = c.put("/api/admin/counterparties/CCP_ALPHA/mode", json={"mode": "REJECT"})
        assert mode.status_code == 200
        assert c.post("/api/admin/sweep").status_code == 200


def test_websocket_streams_snapshot_then_live_events(client: TestClient) -> None:
    with client.websocket_connect("/ws?topics=trade,confirmation") as ws:
        snapshot = ws.receive_json()
        assert snapshot["topic"] == "snapshot" and len(snapshot["data"]["quotes"]) == 5

        client.post("/api/orders", json=BUY)
        topics = {ws.receive_json()["topic"] for _ in range(4)}  # 1 trade + 3 confirmations
    assert topics == {"trade", "confirmation"}


def test_websocket_topic_filter_excludes_other_topics(client: TestClient) -> None:
    with client.websocket_connect("/ws?topics=alert") as ws:
        ws.receive_json()  # snapshot
        client.post("/api/orders", json=BUY)  # produces trade/order/confirmation, no alert
        client.post("/api/admin/trades/inject", json={**BUY, "price": "1.2"})
        event = ws.receive_json()
    assert event["topic"] == "alert" and event["data"]["rule"] == "OFF_MARKET_PRICE"
