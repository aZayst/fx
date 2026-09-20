from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from .. import services, trading
from ..context import AppContext, get_ctx
from ..models import Order
from ..posttrade import dispatch_trade
from ..pricing import UnknownInstrument
from ..schemas import OrderIn, OrderOut

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=201)
def create_order(
    req: OrderIn, background: BackgroundTasks, ctx: AppContext = Depends(get_ctx)
) -> OrderOut:
    """Business rejections (size limits) still return 201 with status=REJECTED."""
    try:
        order, trade_id = services.place_order(ctx, req)
    except trading.UnknownAccount:
        raise HTTPException(404, f"unknown account {req.account_id}") from None
    except UnknownInstrument:
        raise HTTPException(404, f"unknown instrument {req.symbol}") from None
    if trade_id is not None:
        # Runs after the response, so slow counterparties never block the trader.
        background.add_task(dispatch_trade, ctx.db, ctx.client, ctx.settings, trade_id)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(
    account_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_ctx),
) -> list[OrderOut]:
    stmt = select(Order).order_by(Order.id.desc()).limit(min(limit, 500))
    if account_id:
        stmt = stmt.where(Order.account_id == account_id)
    if status:
        stmt = stmt.where(Order.status == status)
    with ctx.db.session() as s:
        return [OrderOut.model_validate(o) for o in s.scalars(stmt)]


@router.delete("/{order_id}", response_model=OrderOut)
def cancel_order(order_id: int, ctx: AppContext = Depends(get_ctx)) -> OrderOut:
    with ctx.db.session() as s:
        try:
            return OrderOut.model_validate(trading.cancel_order(s, order_id))
        except trading.OrderNotFound:
            raise HTTPException(404, "order not found") from None
        except trading.OrderNotCancellable as exc:
            raise HTTPException(409, str(exc)) from None
