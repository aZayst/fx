"""Order entry and execution.

Simple dealer model: MARKET orders fill immediately at the current ask (BUY) or
bid (SELL). LIMIT orders fill immediately if marketable, otherwise rest as OPEN
and are re-checked on every price tick.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import metrics
from .booking import book_trade
from .config import Settings
from .db import queue_event
from .models import Account, Order, OrderStatus, OrderType, Side, Trade, utcnow
from .pricing import PriceFeed, Quote, notional_usd
from .schemas import OrderIn, OrderOut


class UnknownAccount(Exception):
    pass


class OrderNotFound(Exception):
    pass


class OrderNotCancellable(Exception):
    pass


def _publish(session: Session, order: Order) -> None:
    queue_event(session, "order", OrderOut.model_validate(order).model_dump(mode="json"))


def fill_price_for(order: Order, quote: Quote) -> Decimal | None:
    """The price this order would fill at right now, or None if it cannot fill."""
    if order.order_type == OrderType.MARKET:
        return quote.ask if order.side == Side.BUY else quote.bid
    assert order.limit_price is not None
    if order.side == Side.BUY and order.limit_price >= quote.ask:
        return quote.ask
    if order.side == Side.SELL and order.limit_price <= quote.bid:
        return quote.bid
    return None


def _fill(session: Session, order: Order, price: Decimal, now: datetime) -> Trade:
    order.status = OrderStatus.FILLED
    order.fill_price = price
    order.filled_at = now
    trade = book_trade(
        session,
        account_id=order.account_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        price=price,
        order_id=order.id,
        now=now,
    )
    metrics.ORDERS.labels(status="FILLED").inc()
    _publish(session, order)
    return trade


def _reject(session: Session, order: Order, reason: str) -> None:
    order.status = OrderStatus.REJECTED
    order.reject_reason = reason
    metrics.ORDERS.labels(status="REJECTED").inc()
    _publish(session, order)


def submit_order(
    session: Session,
    feed: PriceFeed,
    settings: Settings,
    req: OrderIn,
    now: datetime | None = None,
) -> tuple[Order, Trade | None]:
    """Validate, persist and (if possible) execute an order.

    Unknown account/instrument raise (nothing is stored). Business-rule failures
    (size limits) are stored as REJECTED orders so they show up in the audit trail.
    """
    now = now or utcnow()
    if session.get(Account, req.account_id) is None:
        raise UnknownAccount(req.account_id)
    quote = feed.quote(req.symbol)  # raises UnknownInstrument

    order = Order(
        account_id=req.account_id,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=req.quantity,
        limit_price=req.limit_price,
        status=OrderStatus.OPEN,
        created_at=now,
    )
    session.add(order)
    session.flush()

    if not settings.min_order_qty <= req.quantity <= settings.max_order_qty:
        _reject(
            session, order, f"quantity must be {settings.min_order_qty}..{settings.max_order_qty}"
        )
        return order, None
    if notional_usd(req.symbol, req.quantity, quote.mid) > settings.max_order_notional_usd:
        _reject(session, order, f"notional exceeds USD {settings.max_order_notional_usd}")
        return order, None

    price = fill_price_for(order, quote)
    if price is None:
        metrics.ORDERS.labels(status="OPEN").inc()
        _publish(session, order)
        return order, None
    return order, _fill(session, order, price, now)


def match_open_orders(
    session: Session, feed: PriceFeed, now: datetime | None = None
) -> list[Trade]:
    """Called on every tick: fill resting limit orders the market has reached."""
    now = now or utcnow()
    trades = []
    for order in session.scalars(select(Order).where(Order.status == OrderStatus.OPEN)):
        price = fill_price_for(order, feed.quote(order.symbol))
        if price is not None:
            trades.append(_fill(session, order, price, now))
    return trades


def cancel_order(session: Session, order_id: int) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise OrderNotFound(order_id)
    if order.status != OrderStatus.OPEN:
        raise OrderNotCancellable(f"order {order_id} is {order.status}")
    order.status = OrderStatus.CANCELLED
    metrics.ORDERS.labels(status="CANCELLED").inc()
    _publish(session, order)
    return order
