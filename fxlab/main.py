"""App factory. Run with:  uvicorn fxlab.main:app --reload"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import monitoring, services
from .config import Settings
from .context import AppContext, get_ctx
from .counterparty_client import CounterpartyClient, HttpCounterpartyClient
from .db import Database
from .events import EventBus
from .posttrade import dispatch_trade
from .pricing import PriceFeed
from .routes import admin, market, orders, posttrade, trades, ws
from .routes import monitoring as monitoring_routes
from .schemas import QuoteOut
from .seed import seed_accounts

log = logging.getLogger("fxlab")
STATIC = Path(__file__).parent / "static"


async def _ticker(ctx: AppContext) -> None:
    """Every tick: move prices, push them to browsers, fill resting limit orders."""
    while True:
        await asyncio.sleep(ctx.settings.tick_interval_seconds)
        try:
            quotes = ctx.feed.tick()
            ctx.bus.publish(
                "quote", [QuoteOut(**q.__dict__).model_dump(mode="json") for q in quotes]
            )
            # DB + HTTP calls are blocking, so keep them off the event loop.
            for trade_id in await asyncio.to_thread(services.run_matching, ctx):
                await asyncio.to_thread(dispatch_trade, ctx.db, ctx.client, ctx.settings, trade_id)
        except Exception:
            log.exception("ticker iteration failed")


def _sweep(ctx: AppContext) -> None:
    with ctx.db.session() as s:
        monitoring.sweep_unacked(s, ctx.settings)


async def _sweeper(ctx: AppContext) -> None:
    """Every few seconds: alert on confirmations that missed their ack SLA."""
    while True:
        await asyncio.sleep(ctx.settings.sweep_interval_seconds)
        try:
            await asyncio.to_thread(_sweep, ctx)
        except Exception:
            log.exception("sweeper iteration failed")


def create_app(
    settings: Settings | None = None,
    client: CounterpartyClient | None = None,
    feed: PriceFeed | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    bus = EventBus()
    ctx = AppContext(
        settings=settings,
        db=Database(settings.database_url, bus),
        bus=bus,
        feed=feed or PriceFeed(),
        client=client or HttpCounterpartyClient(settings.counterparty_url, settings.api_key),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        ctx.db.create_all()
        seed_accounts(ctx.db)
        tasks = []
        if settings.background_tasks:
            tasks = [asyncio.create_task(_ticker(ctx)), asyncio.create_task(_sweeper(ctx))]
        yield
        for t in tasks:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t

    app = FastAPI(title="fxlab", version="0.1.0", lifespan=lifespan)
    app.state.ctx = ctx

    for module in (market, orders, trades, posttrade, monitoring_routes, admin):
        app.include_router(module.router)
    app.include_router(ws.router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    def ready(ctx: AppContext = Depends(get_ctx)) -> dict[str, str]:
        """Readiness: dependencies (the database) are reachable."""
        try:
            ctx.db.check()
        except Exception as exc:
            raise HTTPException(503, f"database unavailable: {exc}") from None
        return {"status": "ready"}

    @app.get("/metrics", tags=["system"])
    def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
