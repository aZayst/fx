"""Request/response shapes (Pydantic). These are the public API contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import OrderType, Side


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountOut(ORM):
    id: str
    name: str


class OrderIn(BaseModel):
    account_id: str
    symbol: str = Field(examples=["EUR/USD"])
    side: Side
    quantity: Decimal = Field(gt=0, description="Base-currency amount")
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _limit_needs_price(self) -> OrderIn:
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        return self


class OrderOut(ORM):
    id: int
    account_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    status: str
    reject_reason: str | None
    fill_price: Decimal | None
    created_at: datetime
    filled_at: datetime | None


class TradeOut(ORM):
    id: int
    trade_ref: str
    order_id: int | None
    account_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    quote_quantity: Decimal
    base_ccy: str
    quote_ccy: str
    trade_date: date
    value_date: date
    status: str
    source: str
    created_at: datetime


class ConfirmationOut(ORM):
    id: int
    trade_id: int
    counterparty: str
    attempt: int
    report_id: str
    status: str
    reject_reason: str | None
    sent_at: datetime
    acked_at: datetime | None


class TradeDetail(TradeOut):
    confirmations: list[ConfirmationOut]


class AlertOut(ORM):
    id: int
    rule: str
    severity: str
    subject: str
    message: str
    status: str
    created_at: datetime


class QuoteOut(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    mid: Decimal
    ts: datetime


class InstrumentOut(BaseModel):
    symbol: str
    base: str
    quote: str
    pip_size: Decimal
    spread_pips: Decimal


class PositionOut(BaseModel):
    account_id: str
    symbol: str
    net_base: Decimal  # + long base ccy, - short
    net_quote: Decimal  # cash in quote ccy (negative when we paid for base)
    mid: Decimal
    pnl_quote: Decimal  # mark-to-market P&L in the quote currency
    pnl_usd: Decimal


class AckIn(BaseModel):
    """Trade Capture Report Ack, as sent back by a counterparty."""

    report_id: str
    status: Literal["ACCEPTED", "REJECTED"]
    reject_reason: str | None = None


class AckResult(BaseModel):
    matched: bool
    detail: str


class ResendIn(BaseModel):
    trade_id: int
    counterparty: str | None = Field(default=None, description="Omit to resend to all not-ACKED")


class ManualTradeIn(BaseModel):
    """Inject a trade at an arbitrary price (simulates a bad manual booking)."""

    account_id: str
    symbol: str
    side: Side
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)


class SetMidIn(BaseModel):
    mid: Decimal = Field(gt=0)


class SetModeIn(BaseModel):
    mode: Literal["ACK", "REJECT", "SILENT"]
