"""Use-cases that span several modules: order -> booking -> monitoring.

Routes and background loops call these so the sequence lives in one place:
    1. execute / book inside ONE transaction (order, trade and alerts commit together)
    2. only after commit, send Trade Capture Reports (see posttrade.dispatch_trade)
"""

from __future__ import annotations

from sqlalchemy import select

from . import booking, monitoring, trading
from .context import AppContext
from .models import Order, Trade
from .schemas import ManualTradeIn, OrderIn, OrderOut


def place_order(ctx: AppContext, req: OrderIn) -> tuple[OrderOut, int | None]:
    """Returns the order and, if it filled, the new trade id (to be dispatched)."""
    with ctx.db.session() as s:
        order, trade = trading.submit_order(s, ctx.feed, ctx.settings, req)
        if trade is not None:
            monitoring.check_trade(s, ctx.settings, ctx.feed, trade)
        return OrderOut.model_validate(order), (trade.id if trade else None)


def run_matching(ctx: AppContext) -> list[int]:
    """Fill resting limit orders the market has reached; returns new trade ids."""
    with ctx.db.session() as s:
        if s.scalar(select(Order.id).where(Order.status == "OPEN").limit(1)) is None:
            return []
        trades = trading.match_open_orders(s, ctx.feed)
        for t in trades:
            monitoring.check_trade(s, ctx.settings, ctx.feed, t)
        return [t.id for t in trades]


def inject_trade(ctx: AppContext, req: ManualTradeIn) -> int:
    """Book a trade at an arbitrary price, bypassing execution (demo of a bad booking)."""
    with ctx.db.session() as s:
        trade = booking.book_trade(
            s,
            account_id=req.account_id,
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            price=req.price,
            source="MANUAL",
        )
        monitoring.check_trade(s, ctx.settings, ctx.feed, trade)
        return trade.id


def get_trade(ctx: AppContext, trade_id: int) -> Trade | None:
    with ctx.db.session() as s:
        return s.get(Trade, trade_id)
