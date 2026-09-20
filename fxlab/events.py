"""In-process pub/sub that feeds the WebSocket.

Domain code calls `bus.publish(topic, data)` from ANY thread (request threads,
the background ticker). Each WebSocket connection owns one `Subscription` whose
queue is drained on the event loop. `call_soon_threadsafe` is what makes the
thread -> event-loop hop safe.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(eq=False)  # identity hash: subscriptions live in a set
class Subscription:
    topics: set[str] | None  # None = all topics
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()

    def subscribe(self, topics: set[str] | None = None) -> Subscription:
        sub = Subscription(topics=topics, loop=asyncio.get_running_loop())
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, topic: str, data: Any) -> None:
        event = {"topic": topic, "data": data, "ts": datetime.now(UTC).isoformat()}
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if sub.topics is not None and topic not in sub.topics:
                continue
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, event)
            except RuntimeError:  # loop already closed (shutdown)
                self.unsubscribe(sub)
