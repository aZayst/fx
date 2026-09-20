"""The timer-driven parts: price ticker (fills resting orders) and the SLA sweeper."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from fxlab.config import Settings
from fxlab.counterparty_client import HttpCounterpartyClient
from fxlab.main import create_app
from fxlab.pricing import PriceFeed

from .fakes import FakeClient


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_ticker_fills_a_resting_limit_order_and_dispatches_it(fake_client: FakeClient) -> None:
    settings = Settings(database_url="sqlite://", background_tasks=True, tick_interval_seconds=0.05)
    with TestClient(create_app(settings, fake_client, PriceFeed(seed=1))) as c:
        order = c.post(
            "/api/orders",
            json={
                "account_id": "FUND-1",
                "symbol": "EUR/USD",
                "side": "BUY",
                "quantity": "1000000",
                "order_type": "LIMIT",
                "limit_price": "1.0000",
            },
        ).json()
        assert order["status"] == "OPEN"

        c.post("/api/admin/prices/EUR/USD", json={"mid": "0.9900"})  # market drops to the limit

        wait_until(lambda: c.get("/api/orders", params={"status": "FILLED"}).json() != [])
        wait_until(lambda: len(fake_client.sent) == 3)  # ticker also sent the TCRs
        assert c.get("/api/trades").json()[0]["order_id"] == order["id"]


def test_sweeper_raises_unacked_alert_when_no_ack_arrives(fake_client: FakeClient) -> None:
    settings = Settings(
        database_url="sqlite://",
        background_tasks=True,
        tick_interval_seconds=10,
        sweep_interval_seconds=0.05,
        ack_sla_seconds=0,  # any unacked report older than 1s is a break
    )
    with TestClient(create_app(settings, fake_client, PriceFeed(seed=1))) as c:
        c.post(
            "/api/orders",
            json={
                "account_id": "FUND-1",
                "symbol": "EUR/USD",
                "side": "BUY",
                "quantity": "1000000",
            },
        )
        wait_until(
            lambda: {a["rule"] for a in c.get("/api/alerts").json()} == {"UNACKED_TOO_LONG"},
            timeout=6,
        )
        assert len(c.get("/api/alerts").json()) == 3  # one per silent counterparty


def test_http_client_speaks_the_counterparty_protocol() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"code": "CCP_ALPHA", "mode": "ACK"}])
        if request.url.path.endswith("/mode"):
            return httpx.Response(200, json={"code": "CCP_ALPHA", "mode": "SILENT"})
        return httpx.Response(202, json={"received": "r1"})

    client = HttpCounterpartyClient(
        "http://cp", api_key="k", transport=httpx.MockTransport(handler)
    )
    client.send_tcr("CCP_ALPHA", {"report_id": "r1"})
    assert client.get_modes() == {"CCP_ALPHA": "ACK"}
    assert client.set_mode("CCP_ALPHA", "SILENT")["mode"] == "SILENT"
    assert [r.url.path for r in seen] == ["/tcr/CCP_ALPHA", "/agencies", "/agencies/CCP_ALPHA/mode"]
    assert all(r.headers["x-api-key"] == "k" for r in seen)


def test_http_client_raises_on_error_status_so_the_gateway_can_log_it() -> None:
    client = HttpCounterpartyClient(
        "http://cp", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.send_tcr("CCP_ALPHA", {"report_id": "r1"})
