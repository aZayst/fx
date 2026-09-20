from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fxlab.booking import book_trade
from fxlab.config import Settings
from fxlab.context import AppContext
from fxlab.db import Base, Database
from fxlab.events import EventBus
from fxlab.main import create_app
from fxlab.models import Trade
from fxlab.pricing import PriceFeed
from fxlab.seed import seed_accounts

from .fakes import FakeClient


@pytest.fixture
def settings() -> Settings:
    # In-memory SQLite by default; CI also runs the suite on Postgres by setting
    # TEST_DATABASE_URL. No background loops: tests drive everything explicitly.
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    return Settings(database_url=url, background_tasks=False)


@pytest.fixture
def feed() -> PriceFeed:
    return PriceFeed(seed=42)


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings.database_url, EventBus())
    Base.metadata.drop_all(database.engine)  # clean slate even if a previous run crashed
    database.create_all()
    seed_accounts(database)
    yield database
    Base.metadata.drop_all(database.engine)


@pytest.fixture
def fake_client(settings: Settings) -> FakeClient:
    return FakeClient(settings.counterparties)


@pytest.fixture
def app(settings: Settings, fake_client: FakeClient, feed: PriceFeed) -> FastAPI:
    return create_app(settings, fake_client, feed)


@pytest.fixture
def client(app: FastAPI, ctx: AppContext) -> Iterator[TestClient]:
    Base.metadata.drop_all(ctx.db.engine)
    with TestClient(app) as c:  # `with` runs startup: create tables + seed accounts
        yield c
    Base.metadata.drop_all(ctx.db.engine)


@pytest.fixture
def ctx(app: FastAPI) -> AppContext:
    context: AppContext = app.state.ctx
    return context


def make_trade(db: Database, **overrides: object) -> Trade:
    """Book a trade directly (bypassing order entry) and return it."""
    params: dict[str, object] = {
        "account_id": "FUND-1",
        "symbol": "EUR/USD",
        "side": "BUY",
        "quantity": Decimal("1000000"),
        "price": Decimal("1.0854"),
    }
    params.update(overrides)
    with db.session() as s:
        return book_trade(s, **params)  # type: ignore[arg-type]
