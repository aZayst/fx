"""Database tables and the string enums used as their status columns."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    OPEN = "OPEN"  # resting limit order
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradeStatus(StrEnum):
    BOOKED = "BOOKED"  # captured, waiting for counterparty confirmations
    CONFIRMED = "CONFIRMED"  # every counterparty acked
    SETTLED = "SETTLED"  # value date reached
    CANCELLED = "CANCELLED"


class ConfStatus(StrEnum):
    SENT = "SENT"
    ACKED = "ACKED"
    REJECTED = "REJECTED"
    NOT_SENT = "NOT_SENT"  # never a stored value: used by reconciliation for absences


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Always hand back timezone-aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(6))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(10), index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_ref: Mapped[str] = mapped_column(String(40), unique=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 2))  # base currency amount
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    quote_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 4))  # quantity * price
    base_ccy: Mapped[str] = mapped_column(String(3))
    quote_ccy: Mapped[str] = mapped_column(String(3))
    trade_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(10), default="ORDER")  # ORDER | MANUAL
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Confirmation(Base):
    """One row per (trade, counterparty): the Trade Capture Report we sent and its ack."""

    __tablename__ = "confirmations"
    __table_args__ = (UniqueConstraint("trade_id", "counterparty"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), index=True)
    counterparty: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(default=1)
    # Must be unique per send, so every resend mints a fresh id (see posttrade.py).
    report_id: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(10), index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    subject: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(String(300))
    # rule + subject: raising the same alert twice is a no-op.
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(14), default=AlertStatus.OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
