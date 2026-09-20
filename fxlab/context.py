"""The handful of shared objects the app needs, created once in `create_app`."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .config import Settings
from .counterparty_client import CounterpartyClient
from .db import Database
from .events import EventBus
from .pricing import PriceFeed


@dataclass
class AppContext:
    settings: Settings
    db: Database
    bus: EventBus
    feed: PriceFeed
    client: CounterpartyClient


def get_ctx(request: Request) -> AppContext:
    """FastAPI dependency: `ctx: AppContext = Depends(get_ctx)`."""
    ctx: AppContext = request.app.state.ctx
    return ctx
