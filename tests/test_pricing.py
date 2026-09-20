from decimal import Decimal

import pytest

from fxlab.pricing import PriceFeed, UnknownInstrument, notional_usd, quote_to_usd


def test_quote_has_spread_around_mid(feed: PriceFeed) -> None:
    q = feed.quote("EUR/USD")
    assert q.bid < q.mid < q.ask
    assert q.ask - q.bid == Decimal("0.00008")  # 0.8 pips


def test_jpy_pair_uses_three_decimals(feed: PriceFeed) -> None:
    q = feed.quote("USD/JPY")
    assert q.bid.as_tuple().exponent == -3


def test_tick_moves_prices_and_is_reproducible_with_a_seed() -> None:
    a, b = PriceFeed(seed=7), PriceFeed(seed=7)
    before = a.quote("EUR/USD").mid
    a.tick()
    b.tick()
    assert a.quote("EUR/USD").mid == b.quote("EUR/USD").mid
    assert a.quote("EUR/USD").mid != before


def test_set_mid(feed: PriceFeed) -> None:
    feed.set_mid("EUR/USD", Decimal("1.2"))
    assert feed.quote("EUR/USD").mid == Decimal("1.20000")


def test_unknown_instrument(feed: PriceFeed) -> None:
    with pytest.raises(UnknownInstrument):
        feed.quote("XXX/YYY")


def test_notional_usd() -> None:
    assert notional_usd("EUR/USD", Decimal(1_000_000), Decimal("1.10")) == Decimal("1100000.00")
    assert notional_usd("USD/JPY", Decimal(1_000_000), Decimal("156")) == Decimal(1_000_000)


def test_quote_to_usd_converts_jpy() -> None:
    assert quote_to_usd("USD/JPY", Decimal(1_560_000), Decimal("156")) == Decimal(10_000)
