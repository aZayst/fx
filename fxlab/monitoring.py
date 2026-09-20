"""Transaction monitoring: reconciliation view, breaks, and alert rules.

Two kinds of monitoring live here:
  * OPERATIONAL  - reconciliation: what did we expect (every trade x every
    counterparty) versus what actually got acked. Absences count as breaks.
  * RULE ALERTS  - simple checks on each trade (size, price, burst) plus SLA
    checks on confirmations that are still waiting for an ack.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import metrics
from .config import Settings
from .db import queue_event
from .models import Alert, Confirmation, ConfStatus, Trade, TradeStatus, utcnow
from .pricing import PriceFeed, notional_usd
from .schemas import AlertOut

# ------------------------------------------------------------------ reconciliation


def reconciliation(
    session: Session,
    settings: Settings,
    now: datetime | None = None,
    limit: int = 50,
    breaks_only: bool = False,
) -> list[dict[str, Any]]:
    """One row per (recent trade x expected counterparty).

    The key idea: enumerate what SHOULD exist, then look up what does. A pair with
    no Confirmation row is reported as NOT_SENT rather than silently missing.
    """
    now = now or utcnow()
    trades = session.scalars(
        select(Trade)
        .where(Trade.status != TradeStatus.CANCELLED)
        .order_by(Trade.id.desc())
        .limit(limit)
    ).all()
    confs: dict[tuple[int, str], Confirmation] = {}
    if trades:
        for found in session.scalars(
            select(Confirmation).where(Confirmation.trade_id.in_([t.id for t in trades]))
        ):
            confs[(found.trade_id, found.counterparty)] = found

    rows: list[dict[str, Any]] = []
    for t in trades:
        for cp in settings.counterparties:
            c = confs.get((t.id, cp))
            status = c.status if c else ConfStatus.NOT_SENT
            since = c.sent_at if c else t.created_at
            latency = (
                int((c.acked_at - c.sent_at).total_seconds() * 1000) if c and c.acked_at else None
            )
            row: dict[str, Any] = {
                "trade_id": t.id,
                "trade_ref": t.trade_ref,
                "symbol": t.symbol,
                "side": t.side,
                "quantity": str(t.quantity),
                "price": str(t.price),
                "counterparty": cp,
                "status": status,
                "attempt": c.attempt if c else 0,
                "report_id": c.report_id if c else None,
                "reject_reason": c.reject_reason if c else None,
                "sent_at": c.sent_at.isoformat() if c else None,
                "acked_at": c.acked_at.isoformat() if c and c.acked_at else None,
                "ack_latency_ms": latency,
                "age_seconds": int((now - since).total_seconds()),
                "is_break": status != ConfStatus.ACKED,
            }
            row["stuck"] = (
                status in (ConfStatus.SENT, ConfStatus.NOT_SENT)
                and row["age_seconds"] > settings.ack_sla_seconds
            )
            if row["is_break"] or not breaks_only:
                rows.append(row)
    return rows


def summary(session: Session, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    rows = reconciliation(session, settings, now, limit=500)
    by_cp: dict[str, dict[str, int]] = {cp: {} for cp in settings.counterparties}
    for r in rows:
        by_cp[r["counterparty"]][r["status"]] = by_cp[r["counterparty"]].get(r["status"], 0) + 1
    trades_by_status: dict[str, int] = {
        status: n
        for status, n in session.execute(select(Trade.status, func.count()).group_by(Trade.status))
    }
    open_alerts = session.scalar(
        select(func.count()).select_from(Alert).where(Alert.status == "OPEN")
    )
    return {
        "counterparties": by_cp,
        "breaks": sum(1 for r in rows if r["is_break"]),
        "stuck": sum(1 for r in rows if r["stuck"]),
        "trades_by_status": trades_by_status,
        "open_alerts": open_alerts or 0,
    }


# ------------------------------------------------------------------ alerts


def raise_alert(
    session: Session, rule: str, severity: str, subject: str, message: str
) -> Alert | None:
    """Create an alert unless the same rule+subject already raised one."""
    key = f"{rule}:{subject}"
    if session.scalar(select(Alert.id).where(Alert.dedupe_key == key)) is not None:
        return None
    alert = Alert(
        rule=rule, severity=severity, subject=subject, message=message[:300], dedupe_key=key
    )
    try:
        with session.begin_nested():  # savepoint: a concurrent duplicate must not kill the txn
            session.add(alert)
    except IntegrityError:
        return None
    metrics.ALERTS.labels(rule=rule).inc()
    queue_event(session, "alert", AlertOut.model_validate(alert).model_dump(mode="json"))
    return alert


def check_trade(
    session: Session,
    settings: Settings,
    feed: PriceFeed,
    trade: Trade,
    now: datetime | None = None,
) -> list[Alert]:
    """Run the per-trade rules right after booking."""
    now = now or utcnow()
    raised: list[Alert | None] = []

    # Rule 1: LARGE_NOTIONAL - trade bigger than the desk-review threshold.
    usd = notional_usd(trade.symbol, trade.quantity, trade.price)
    if usd >= settings.large_notional_usd:
        raised.append(
            raise_alert(
                session, "LARGE_NOTIONAL", "MEDIUM", trade.trade_ref,
                f"{trade.trade_ref} notional USD {usd:,.0f} >= {settings.large_notional_usd:,.0f}",
            )
        )  # fmt: skip

    # Rule 2: OFF_MARKET - executed price far from the current mid (fat finger / stale price).
    mid = feed.quote(trade.symbol).mid
    deviation_bps = abs(trade.price - mid) / mid * Decimal(10000)
    if deviation_bps > settings.off_market_bps:
        raised.append(
            raise_alert(
                session, "OFF_MARKET_PRICE", "HIGH", trade.trade_ref,
                f"{trade.trade_ref} at {trade.price} is {deviation_bps:.1f} bps from mid {mid}",
            )
        )  # fmt: skip

    # Rule 3: RAPID_REPEAT - same account/pair/side firing many trades in a short window.
    since = now - timedelta(seconds=settings.rapid_repeat_window_seconds)
    count = session.scalar(
        select(func.count()).select_from(Trade).where(
            Trade.account_id == trade.account_id,
            Trade.symbol == trade.symbol,
            Trade.side == trade.side,
            Trade.status != TradeStatus.CANCELLED,
            Trade.created_at >= since,
        )
    )  # fmt: skip
    if (count or 0) >= settings.rapid_repeat_count:
        raised.append(
            raise_alert(
                session, "RAPID_REPEAT", "MEDIUM", trade.trade_ref,
                f"{count} {trade.side} {trade.symbol} trades by {trade.account_id} "
                f"in {settings.rapid_repeat_window_seconds}s",
            )
        )  # fmt: skip
    return [a for a in raised if a is not None]


def sweep_unacked(session: Session, settings: Settings, now: datetime | None = None) -> list[Alert]:
    """Periodic SLA check: alert on confirmations still waiting (or never sent)."""
    now = now or utcnow()
    raised = []
    for row in reconciliation(session, settings, now, limit=500):
        if not row["stuck"]:
            continue
        if row["status"] == ConfStatus.SENT:
            alert = raise_alert(
                session, "UNACKED_TOO_LONG", "HIGH", row["report_id"],
                f"{row['trade_ref']}: no ack from {row['counterparty']} "
                f"after {row['age_seconds']}s (SLA {settings.ack_sla_seconds}s)",
            )  # fmt: skip
        else:  # NOT_SENT
            alert = raise_alert(
                session, "NOT_DISPATCHED", "HIGH", f"{row['trade_ref']}:{row['counterparty']}",
                f"{row['trade_ref']} was never sent to {row['counterparty']}",
            )  # fmt: skip
        if alert:
            raised.append(alert)
    return raised
