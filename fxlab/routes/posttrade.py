from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from .. import posttrade
from ..context import AppContext, get_ctx
from ..models import Confirmation, Trade
from ..schemas import AckIn, AckResult, ConfirmationOut, ResendIn
from ..security import require_api_key

router = APIRouter(prefix="/api/posttrade", tags=["post-trade"])


@router.post("/acks", response_model=AckResult, dependencies=[Depends(require_api_key)])
def receive_ack(ack: AckIn, ctx: AppContext = Depends(get_ctx)) -> AckResult:
    """Callback used by counterparties to return a Trade Capture Report Ack."""
    with ctx.db.session() as s:
        return posttrade.handle_ack(s, ctx.settings, ack)


@router.post("/resend")
def resend(
    req: ResendIn, background: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> dict[str, object]:
    """Resend the report to one counterparty, or to every one that has not ACKED."""
    with ctx.db.session() as s:
        trade = s.get(Trade, req.trade_id)
        if trade is None:
            raise HTTPException(404, "trade not found")
        try:
            targets = posttrade.targets_for(s, ctx.settings, trade, req.counterparty)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if targets:
        # The DB rows are (re)written inside dispatch_trade, then TCRs go out.
        background.add_task(
            posttrade.dispatch_trade,
            ctx.db,
            ctx.client,
            ctx.settings,
            req.trade_id,
            req.counterparty,
        )
    return {"trade_id": req.trade_id, "resent_to": targets}


@router.get("/confirmations", response_model=list[ConfirmationOut])
def list_confirmations(
    trade_id: int | None = None, ctx: AppContext = Depends(get_ctx)
) -> list[ConfirmationOut]:
    stmt = select(Confirmation).order_by(Confirmation.id.desc()).limit(500)
    if trade_id is not None:
        stmt = stmt.where(Confirmation.trade_id == trade_id)
    with ctx.db.session() as s:
        return [ConfirmationOut.model_validate(c) for c in s.scalars(stmt)]
