"""Instruments and a simulated price feed (a random walk around a start rate)."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


class UnknownInstrument(KeyError):
    pass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    base: str
    quote: str
    pip_size: Decimal
    decimals: int  # price decimals (5 for most pairs, 3 for JPY pairs)
    spread_pips: Decimal
    start_mid: Decimal


def _inst(symbol: str, pip: str, dec: int, spread: str, mid: str) -> Instrument:
    base, quote = symbol.split("/")
    return Instrument(symbol, base, quote, Decimal(pip), dec, Decimal(spread), Decimal(mid))


INSTRUMENTS: dict[str, Instrument] = {
    i.symbol: i
    for i in [
        _inst("EUR/USD", "0.0001", 5, "0.8", "1.08500"),
        _inst("GBP/USD", "0.0001", 5, "1.0", "1.27000"),
        _inst("USD/JPY", "0.01", 3, "0.9", "156.300"),
        _inst("AUD/USD", "0.0001", 5, "1.2", "0.66500"),
        _inst("USD/CHF", "0.0001", 5, "1.2", "0.89500"),
    ]
}


def get_instrument(symbol: str) -> Instrument:
    try:
        return INSTRUMENTS[symbol]
    except KeyError:
        raise UnknownInstrument(symbol) from None


def notional_usd(symbol: str, quantity: Decimal, price: Decimal) -> Decimal:
    """USD value of `quantity` of the base currency. Only USD pairs are supported."""
    inst = get_instrument(symbol)
    if inst.base == "USD":
        return quantity
    if inst.quote == "USD":
        return quantity * price
    raise ValueError(f"no USD conversion for cross pair {symbol}")


def quote_to_usd(symbol: str, amount: Decimal, price: Decimal) -> Decimal:
    """Convert an amount in the pair's quote currency to USD."""
    inst = get_instrument(symbol)
    if inst.quote == "USD":
        return amount
    if inst.base == "USD":
        return amount / price
    raise ValueError(f"no USD conversion for cross pair {symbol}")


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    mid: Decimal
    ts: datetime


class PriceFeed:
    """Holds the current mid per instrument. Thread-safe: the background ticker
    writes, request threads read."""

    def __init__(self, seed: int | None = None, volatility_pips: float = 1.0) -> None:
        self._rng = random.Random(seed)
        self._vol = volatility_pips
        self._lock = threading.Lock()
        self._mids = {s: i.start_mid for s, i in INSTRUMENTS.items()}
        self._ts = datetime.now(UTC)

    @staticmethod
    def _round(inst: Instrument, value: Decimal) -> Decimal:
        return value.quantize(Decimal(1).scaleb(-inst.decimals))

    def _quote(self, inst: Instrument, mid: Decimal) -> Quote:
        half = inst.spread_pips * inst.pip_size / 2
        return Quote(
            inst.symbol,
            self._round(inst, mid - half),
            self._round(inst, mid + half),
            mid,
            self._ts,
        )

    def tick(self) -> list[Quote]:
        """Move every mid by a small random step and return the new quotes."""
        with self._lock:
            self._ts = datetime.now(UTC)
            for sym, inst in INSTRUMENTS.items():
                step = Decimal(str(round(self._rng.gauss(0, self._vol), 3))) * inst.pip_size
                self._mids[sym] = self._round(inst, self._mids[sym] + step)
            return [self._quote(INSTRUMENTS[s], m) for s, m in self._mids.items()]

    def quote(self, symbol: str) -> Quote:
        inst = get_instrument(symbol)
        with self._lock:
            return self._quote(inst, self._mids[symbol])

    def quotes(self) -> list[Quote]:
        with self._lock:
            return [self._quote(INSTRUMENTS[s], m) for s, m in self._mids.items()]

    def set_mid(self, symbol: str, mid: Decimal) -> Quote:
        inst = get_instrument(symbol)
        with self._lock:
            self._mids[symbol] = self._round(inst, mid)
            return self._quote(inst, self._mids[symbol])
