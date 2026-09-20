"""Scenario controls for demos and exercises (fake data only, so deliberately no auth)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from .. import booking, monitoring, services
from ..context import AppContext, get_ctx
from ..posttrade import dispatch_trade
from ..pricing import UnknownInstrument
from ..schemas import ManualTradeIn, QuoteOut, SetMidIn, SetModeIn

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/counterparties")
def counterparties(ctx: AppContext = Depends(get_ctx)) -> dict[str, str]:
    try:
        return ctx.client.get_modes()
    except Exception as exc:
        raise HTTPException(503, f"counterparty service unreachable: {exc}") from None


@router.put("/counterparties/{code}/mode")
def set_counterparty_mode(
    code: str, body: SetModeIn, ctx: AppContext = Depends(get_ctx)
) -> dict[str, str]:
    """ACK = accept, REJECT = reject, SILENT = never answer (creates unacked breaks)."""
    if code not in ctx.settings.counterparties:
        raise HTTPException(404, f"unknown counterparty {code}")
    try:
        return ctx.client.set_mode(code, body.mode)
    except Exception as exc:
        raise HTTPException(503, f"counterparty service unreachable: {exc}") from None


@router.post("/prices/{symbol:path}", response_model=QuoteOut)
def set_price(symbol: str, body: SetMidIn, ctx: AppContext = Depends(get_ctx)) -> QuoteOut:
    try:
        quote = ctx.feed.set_mid(symbol, body.mid)
    except UnknownInstrument:
        raise HTTPException(404, f"unknown instrument {symbol}") from None
    ctx.bus.publish("quote", [QuoteOut(**quote.__dict__).model_dump(mode="json")])
    return QuoteOut(**quote.__dict__)


@router.post("/trades/inject")
def inject_trade(
    req: ManualTradeIn, background: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict[str, int]:
    try:
        trade_id = services.inject_trade(ctx, req)
    except UnknownInstrument:
        raise HTTPException(404, f"unknown instrument {req.symbol}") from None
    background.add_task(dispatch_trade, ctx.db, ctx.client, ctx.settings, trade_id)
    return {"trade_id": trade_id}


@router.post("/settle")
def settle(as_of: date | None = None, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Settle CONFIRMED trades whose value date <= as_of (default today). Pass a future
    date to demo settlement without waiting two days."""
    as_of = as_of or datetime.now(UTC).date()
    with ctx.db.session() as s:
        settled = booking.settle_due_trades(s, as_of)
        return {"as_of": as_of.isoformat(), "settled": [t.trade_ref for t in settled]}


@router.post("/sweep")
def sweep(ctx: AppContext = Depends(get_ctx)) -> dict[str, int]:
    """Run the unacked-trade SLA check now instead of waiting for the timer."""
    with ctx.db.session() as s:
        return {"alerts_raised": len(monitoring.sweep_unacked(s, ctx.settings))}
