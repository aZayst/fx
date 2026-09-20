from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..context import AppContext, get_ctx
from ..models import Account
from ..pricing import INSTRUMENTS, UnknownInstrument
from ..schemas import AccountOut, InstrumentOut, QuoteOut

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments() -> list[InstrumentOut]:
    return [
        InstrumentOut(symbol=i.symbol, base=i.base, quote=i.quote, pip_size=i.pip_size,
                      spread_pips=i.spread_pips)
        for i in INSTRUMENTS.values()
    ]  # fmt: skip


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(ctx: AppContext = Depends(get_ctx)) -> list[AccountOut]:
    with ctx.db.session() as s:
        return [
            AccountOut.model_validate(a) for a in s.scalars(select(Account).order_by(Account.id))
        ]


@router.get("/quotes", response_model=list[QuoteOut])
def list_quotes(ctx: AppContext = Depends(get_ctx)) -> list[QuoteOut]:
    return [QuoteOut(**q.__dict__) for q in ctx.feed.quotes()]


@router.get("/quotes/{symbol:path}", response_model=QuoteOut)
def get_quote(symbol: str, ctx: AppContext = Depends(get_ctx)) -> QuoteOut:
    try:
        return QuoteOut(**ctx.feed.quote(symbol).__dict__)
    except UnknownInstrument:
        raise HTTPException(404, f"unknown instrument {symbol}") from None
