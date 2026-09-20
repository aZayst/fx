from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fxlab import booking
from fxlab.db import Database
from fxlab.models import TradeStatus
from fxlab.pricing import PriceFeed

from .conftest import make_trade


@pytest.mark.parametrize(
    ("trade_day", "expected"),
    [
        (date(2026, 9, 16), date(2026, 9, 18)),  # Wed -> Fri
        (date(2026, 9, 17), date(2026, 9, 21)),  # Thu -> Mon (skips the weekend)
        (date(2026, 9, 18), date(2026, 9, 22)),  # Fri -> Tue
    ],
)
def test_spot_value_date_is_t_plus_2_business_days(trade_day: date, expected: date) -> None:
    assert booking.spot_value_date(trade_day) == expected


def test_trade_ref_and_dates(db: Database) -> None:
    now = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    with db.session() as s:
        t = booking.book_trade(
            s,
            account_id="FUND-1",
            symbol="EUR/USD",
            side="BUY",
            quantity=Decimal(1_000_000),
            price=Decimal("1.0854"),
            now=now,
        )
        assert t.trade_ref == f"TRD-20260917-{t.id:06d}"
        assert t.status == TradeStatus.BOOKED
        assert t.value_date == date(2026, 9, 21)
        assert t.quote_quantity == Decimal("1085400")
        assert (t.base_ccy, t.quote_ccy) == ("EUR", "USD")


def test_state_machine_allows_the_happy_path_and_blocks_the_rest(db: Database) -> None:
    trade = make_trade(db)
    with db.session() as s:
        s.add(trade)
        with pytest.raises(booking.InvalidTransition):
            booking.transition(s, trade, TradeStatus.SETTLED)  # BOOKED -> SETTLED is illegal
        booking.transition(s, trade, TradeStatus.CONFIRMED)
        booking.transition(s, trade, TradeStatus.SETTLED)
        with pytest.raises(booking.InvalidTransition):
            booking.transition(s, trade, TradeStatus.CANCELLED)  # SETTLED is final


def test_settle_due_trades_respects_value_date(db: Database) -> None:
    trade = make_trade(db)  # value date = today + 2 business days
    with db.session() as s:
        s.add(trade)
        booking.transition(s, trade, TradeStatus.CONFIRMED)
        assert booking.settle_due_trades(s, trade.value_date.replace(day=1)) == []
        assert booking.settle_due_trades(s, trade.value_date) == [trade]
        assert trade.status == TradeStatus.SETTLED


def test_unconfirmed_trades_are_never_settled(db: Database) -> None:
    trade = make_trade(db)  # still BOOKED
    with db.session() as s:
        assert booking.settle_due_trades(s, date(2099, 1, 1)) == []
        del trade


def test_position_and_mark_to_market_pnl(db: Database, feed: PriceFeed) -> None:
    make_trade(db, side="BUY", quantity=Decimal(1_000_000), price=Decimal("1.0854"))
    feed.set_mid("EUR/USD", Decimal("1.0900"))
    with db.session() as s:
        [pos] = booking.positions(s, feed, "FUND-1")
    assert pos.net_base == Decimal(1_000_000)
    assert pos.net_quote == Decimal("-1085400")
    # holding 1m EUR now worth 1,090,000 USD, paid 1,085,400 -> +4,600
    assert pos.pnl_quote == Decimal("4600.00")
    assert pos.pnl_usd == Decimal("4600.00")


def test_offsetting_trades_net_to_flat_and_lock_in_pnl(db: Database, feed: PriceFeed) -> None:
    make_trade(db, side="BUY", quantity=Decimal(1_000_000), price=Decimal("1.0850"))
    make_trade(db, side="SELL", quantity=Decimal(1_000_000), price=Decimal("1.0860"))
    with db.session() as s:
        [pos] = booking.positions(s, feed)
    assert pos.net_base == 0
    assert pos.pnl_quote == Decimal("1000.00")  # bought 1.0850, sold 1.0860, 1m EUR


def test_jpy_pnl_is_converted_to_usd(db: Database, feed: PriceFeed) -> None:
    make_trade(db, symbol="USD/JPY", quantity=Decimal(1_000_000), price=Decimal("156.000"))
    with db.session() as s:
        [pos] = booking.positions(s, feed)
    assert pos.pnl_quote != 0
    assert pos.pnl_usd == (pos.pnl_quote / pos.mid).quantize(Decimal("0.01"))
