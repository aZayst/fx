"""WebSocket: pushes live events to the browser.

    ws://host/ws                    -> all topics
    ws://host/ws?topics=quote,trade -> only those topics

Topics: quote, order, trade, confirmation, alert. The first message is a
`snapshot` with the current quotes so a new client can render immediately.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import metrics
from ..schemas import QuoteOut

router = APIRouter()


@router.websocket("/ws")
async def stream(websocket: WebSocket, topics: str | None = None) -> None:
    ctx = websocket.app.state.ctx
    await websocket.accept()
    wanted = {t.strip() for t in topics.split(",") if t.strip()} if topics else None
    sub = ctx.bus.subscribe(wanted)
    metrics.WS_CLIENTS.inc()
    try:
        quotes = [QuoteOut(**q.__dict__).model_dump(mode="json") for q in ctx.feed.quotes()]
        await websocket.send_json({"topic": "snapshot", "data": {"quotes": quotes}})
        while True:
            await websocket.send_json(await sub.queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        ctx.bus.unsubscribe(sub)
        metrics.WS_CLIENTS.dec()
