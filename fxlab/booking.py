"""Booking: turn a fill into a Trade, drive its lifecycle, and compute positions.

Trade lifecycle (the state machine below enforces it):

    BOOKED --all counterparties ack--> CONFIRMED --value date reached--> SETTLED
       \\____________________ CANCELLED (from BOOKED or CONFIRMED) ______/
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import metrics
from .config import Settings
from .db import queue_event
from .models import Confirmation, ConfStatus, Side, Trade, TradeStatus, utcnow
from .pricing import PriceFeed, get_instrument, quote_to_usd
from .schemas import PositionOut, TradeOut


class InvalidTransition(Exception):
    pass


ALLOWED: dict[str, set[str]] = {
    TradeStatus.BOOKED: {TradeStatus.CONFIRMED, TradeStatus.CANCELLED},
    TradeStatus.CONFIRMED: {TradeStatus.SETTLED, TradeStatus.CANCELLED},
    TradeStatus.SETTLED: set(),
    TradeStatus.CANCELLED: set(),
}


def add_business_days(d: date, n: int) -> date:
    """Skip weekends. (Holiday calendars are a curriculum exercise.)"""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def spot_value_date(trade_date: date) -> date:
    return add_business_days(trade_date, 2)  # FX spot settles T+2


def publish_trade(session: Session, trade: Trade) -> None:
    queue_event(session, "trade", TradeOut.model_validate(trade).model_dump(mode="json"))


def book_trade(
    session: Session,
    *,
    account_id: str,
    symbol: str,
    side: Side | str,
    quantity: Decimal,
    price: Decimal,
    order_id: int | None = None,
    source: str = "ORDER",
    now: datetime | None = None,
) -> Trade:
    inst = get_instrument(symbol)
    now = now or utcnow()
    trade_date = now.astimezone(UTC).date()
    trade = Trade(
        trade_ref=f"PENDING-{uuid4().hex}",  # replaced below once we know the id
        order_id=order_id,
        account_id=account_id,
        symbol=symbol,
        side=str(side),
        quantity=quantity,
        price=price,
        quote_quantity=quantity * price,
        base_ccy=inst.base,
        quote_ccy=inst.quote,
        trade_date=trade_date,
        value_date=spot_value_date(trade_date),
        status=TradeStatus.BOOKED,
        source=source,
        created_at=now,
    )
    session.add(trade)
    session.flush()  # assigns trade.id
    trade.trade_ref = f"TRD-{trade_date:%Y%m%d}-{trade.id:06d}"
    session.flush()
    metrics.TRADES_BOOKED.labels(source=source).inc()
    publish_trade(session, trade)
    return trade


def transition(session: Session, trade: Trade, new_status: TradeStatus) -> None:
    if new_status not in ALLOWED[trade.status]:
        raise InvalidTransition(f"{trade.trade_ref}: {trade.status} -> {new_status} not allowed")
    trade.status = new_status
    publish_trade(session, trade)


def refresh_confirmation_status(session: Session, trade: Trade, settings: Settings) -> None:
    """BOOKED -> CONFIRMED once every expected counterparty has ACKED."""
    if trade.status != TradeStatus.BOOKED:
        return
    statuses = {
        c.counterparty: c.status
        for c in session.scalars(select(Confirmation).where(Confirmation.trade_id == trade.id))
    }
    if all(statuses.get(cp) == ConfStatus.ACKED for cp in settings.counterparties):
        transition(session, trade, TradeStatus.CONFIRMED)


def settle_due_trades(session: Session, as_of: date) -> list[Trade]:
    """CONFIRMED trades whose value date has arrived become SETTLED."""
    due = session.scalars(
        select(Trade).where(Trade.status == TradeStatus.CONFIRMED, Trade.value_date <= as_of)
    ).all()
    for trade in due:
        transition(session, trade, TradeStatus.SETTLED)
    return list(due)


def positions(
    session: Session, feed: PriceFeed, account_id: str | None = None
) -> list[PositionOut]:
    """Net position per (account, symbol) and its mark-to-market P&L.

    For every trade: BUY adds base and spends quote; SELL does the opposite.
    P&L = what the base we hold is worth now (net_base * mid) + the cash (net_quote).
    """
    stmt = select(Trade).where(Trade.status != TradeStatus.CANCELLED)
    if account_id:
        stmt = stmt.where(Trade.account_id == account_id)
    net: dict[tuple[str, str], list[Decimal]] = {}
    for t in session.scalars(stmt):
        sign = 1 if t.side == Side.BUY else -1
        base_q = net.setdefault((t.account_id, t.symbol), [Decimal(0), Decimal(0)])
        base_q[0] += sign * t.quantity
        base_q[1] -= sign * t.quote_quantity
    rows = []
    for (acct, symbol), (net_base, net_quote) in sorted(net.items()):
        mid = feed.quote(symbol).mid
        pnl_quote = net_base * mid + net_quote
        rows.append(
            PositionOut(
                account_id=acct,
                symbol=symbol,
                net_base=net_base,
                net_quote=net_quote,
                mid=mid,
                pnl_quote=pnl_quote.quantize(Decimal("0.01")),
                pnl_usd=quote_to_usd(symbol, pnl_quote, mid).quantize(Decimal("0.01")),
            )
        )
    return rows
