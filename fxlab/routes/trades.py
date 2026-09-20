from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import booking
from ..context import AppContext, get_ctx
from ..models import Confirmation, Trade
from ..schemas import ConfirmationOut, PositionOut, TradeDetail, TradeOut

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    account_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_ctx),
) -> list[TradeOut]:
    stmt = select(Trade).order_by(Trade.id.desc()).limit(min(limit, 500))
    if account_id:
        stmt = stmt.where(Trade.account_id == account_id)
    if status:
        stmt = stmt.where(Trade.status == status)
    with ctx.db.session() as s:
        return [TradeOut.model_validate(t) for t in s.scalars(stmt)]


@router.get("/trades/{trade_id}", response_model=TradeDetail)
def get_trade(trade_id: int, ctx: AppContext = Depends(get_ctx)) -> TradeDetail:
    with ctx.db.session() as s:
        trade = s.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(404, "trade not found")
        confs = s.scalars(
            select(Confirmation).where(Confirmation.trade_id == trade_id).order_by(Confirmation.id)
        )
        return TradeDetail(
            **TradeOut.model_validate(trade).model_dump(),
            confirmations=[ConfirmationOut.model_validate(c) for c in confs],
        )


@router.get("/positions", response_model=list[PositionOut])
def get_positions(
    account_id: str | None = None, ctx: AppContext = Depends(get_ctx)
) -> list[PositionOut]:
    with ctx.db.session() as s:
        return booking.positions(s, ctx.feed, account_id)
