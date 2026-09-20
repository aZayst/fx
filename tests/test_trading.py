from __future__ import annotations

from decimal import Decimal

import pytest

from fxlab import trading
from fxlab.config import Settings
from fxlab.db import Database
from fxlab.models import OrderStatus, OrderType, Side
from fxlab.pricing import PriceFeed
from fxlab.schemas import OrderIn


def order_in(**kw: object) -> OrderIn:
    base: dict[str, object] = {
        "account_id": "FUND-1",
        "symbol": "EUR/USD",
        "side": Side.BUY,
        "quantity": Decimal(1_000_000),
    }
    base.update(kw)
    return OrderIn(**base)  # type: ignore[arg-type]


def test_market_buy_fills_at_ask(db: Database, feed: PriceFeed, settings: Settings) -> None:
    with db.session() as s:
        order, trade = trading.submit_order(s, feed, settings, order_in())
        assert order.status == OrderStatus.FILLED
        assert trade is not None and trade.price == feed.quote("EUR/USD").ask


def test_market_sell_fills_at_bid(db: Database, feed: PriceFeed, settings: Settings) -> None:
    with db.session() as s:
        _, trade = trading.submit_order(s, feed, settings, order_in(side=Side.SELL))
        assert trade is not None and trade.price == feed.quote("EUR/USD").bid


def test_limit_below_market_rests_then_fills_when_price_falls(
    db: Database, feed: PriceFeed, settings: Settings
) -> None:
    req = order_in(order_type=OrderType.LIMIT, limit_price=Decimal("1.0800"))
    with db.session() as s:
        order, trade = trading.submit_order(s, feed, settings, req)
        assert order.status == OrderStatus.OPEN and trade is None
        assert trading.match_open_orders(s, feed) == []  # market hasn't reached it

        feed.set_mid("EUR/USD", Decimal("1.0790"))  # ask 1.07904 <= limit 1.0800
        filled = trading.match_open_orders(s, feed)
        assert len(filled) == 1
        assert order.status == OrderStatus.FILLED
        assert order.fill_price == Decimal("1.07904")  # price improvement over the limit


def test_marketable_limit_fills_immediately(
    db: Database, feed: PriceFeed, settings: Settings
) -> None:
    req = order_in(order_type=OrderType.LIMIT, limit_price=Decimal("1.2000"))
    with db.session() as s:
        order, trade = trading.submit_order(s, feed, settings, req)
        assert order.status == OrderStatus.FILLED and trade is not None


def test_quantity_below_minimum_is_rejected_and_recorded(
    db: Database, feed: PriceFeed, settings: Settings
) -> None:
    with db.session() as s:
        order, trade = trading.submit_order(s, feed, settings, order_in(quantity=Decimal(10)))
        assert trade is None
        assert order.status == OrderStatus.REJECTED and "quantity" in (order.reject_reason or "")


def test_notional_over_limit_is_rejected(db: Database, feed: PriceFeed) -> None:
    tight = Settings(database_url="sqlite://", max_order_notional_usd=Decimal(500_000))
    with db.session() as s:
        order, trade = trading.submit_order(s, feed, tight, order_in())
        assert trade is None and order.status == OrderStatus.REJECTED


def test_unknown_account_raises(db: Database, feed: PriceFeed, settings: Settings) -> None:
    with db.session() as s, pytest.raises(trading.UnknownAccount):
        trading.submit_order(s, feed, settings, order_in(account_id="NOPE"))


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValueError, match="limit_price"):
        order_in(order_type=OrderType.LIMIT)


def test_cancel_open_order_only(db: Database, feed: PriceFeed, settings: Settings) -> None:
    req = order_in(order_type=OrderType.LIMIT, limit_price=Decimal("1.0000"))
    with db.session() as s:
        order, _ = trading.submit_order(s, feed, settings, req)
        assert trading.cancel_order(s, order.id).status == OrderStatus.CANCELLED
        with pytest.raises(trading.OrderNotCancellable):
            trading.cancel_order(s, order.id)
        with pytest.raises(trading.OrderNotFound):
            trading.cancel_order(s, 9999)
