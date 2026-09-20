"""Database engine, sessions, and the "publish after commit" hook.

Why the hook? The dashboard re-reads the API whenever it receives an event. If we
published *before* the transaction committed, the browser could read stale data.
So domain code calls `queue_event(session, ...)` and the event only reaches the
bus once the session has really committed (and is dropped on rollback).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .events import EventBus


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # SQLite stores Decimal as float; fine for a training sandbox, Postgres is exact.
        warnings.filterwarnings("ignore", message=".*does \\*not\\* support Decimal.*")
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory DB for all threads
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


class Database:
    def __init__(self, url: str, bus: EventBus) -> None:
        self.engine = make_engine(url)
        self.bus = bus
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        event.listen(self._factory, "after_commit", self._publish_queued)
        event.listen(self._factory, "after_rollback", self._drop_queued)

    def create_all(self) -> None:
        # Importing the models is what registers the tables on Base.metadata. Do it
        # here so create_all() works no matter what the caller happened to import.
        from . import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def check(self) -> None:
        with self.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Open a session; commits on success, rolls back on error."""
        s = self._factory()
        s.info["bus"] = self.bus
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    @staticmethod
    def _publish_queued(session: Session) -> None:
        bus: EventBus | None = session.info.get("bus")
        for topic, data in session.info.pop("events", []):
            if bus is not None:
                bus.publish(topic, data)

    @staticmethod
    def _drop_queued(session: Session) -> None:
        session.info.pop("events", None)


def queue_event(session: Session, topic: str, data: Any) -> None:
    """Publish `data` on `topic` once this session commits."""
    session.info.setdefault("events", []).append((topic, data))
