"""Post-trade: send Trade Capture Reports (TCR) to counterparties and process acks.

Message flow (JSON stand-ins for FIX 35=AE / 35=AR):

    gateway --TradeCaptureReport--> counterparty     (HTTP POST /tcr/{code})
    gateway <--TradeCaptureReportAck-- counterparty  (HTTP POST /api/posttrade/acks)

Key ideas:
  * one Confirmation row per (trade, counterparty), status SENT -> ACKED | REJECTED;
  * `report_id` is unique per send, so a resend mints a new one (attempt + 1) and a
    late ack for the OLD id no longer matches anything and is ignored;
  * the DB commit happens BEFORE the network send, so a fast ack can never arrive
    for a row that doesn't exist yet.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import metrics, monitoring
from .booking import refresh_confirmation_status
from .config import Settings
from .counterparty_client import CounterpartyClient
from .db import Database, queue_event
from .models import Confirmation, ConfStatus, Trade, utcnow
from .schemas import AckIn, AckResult, ConfirmationOut

log = logging.getLogger(__name__)


def make_report_id(trade_ref: str, counterparty: str, attempt: int) -> str:
    return f"{trade_ref}-{counterparty}-{attempt}"


def build_tcr(trade: Trade, conf: Confirmation) -> dict[str, Any]:
    return {
        "msg_type": "TradeCaptureReport",
        "report_id": conf.report_id,
        "counterparty": conf.counterparty,
        "trade_ref": trade.trade_ref,
        "symbol": trade.symbol,
        "side": trade.side,
        "last_qty": str(trade.quantity),
        "last_px": str(trade.price),
        "trade_date": trade.trade_date.isoformat(),
        "value_date": trade.value_date.isoformat(),
        "transact_time": trade.created_at.isoformat(),
    }


def _publish(session: Session, conf: Confirmation) -> None:
    queue_event(
        session, "confirmation", ConfirmationOut.model_validate(conf).model_dump(mode="json")
    )


def targets_for(session: Session, settings: Settings, trade: Trade, only: str | None) -> list[str]:
    """Which counterparties to (re)send to: the named one, else everyone not yet ACKED."""
    if only is not None:
        if only not in settings.counterparties:
            raise ValueError(f"unknown counterparty {only!r}")
        return [only]
    acked = {
        c.counterparty
        for c in session.scalars(
            select(Confirmation).where(
                Confirmation.trade_id == trade.id, Confirmation.status == ConfStatus.ACKED
            )
        )
    }
    return [cp for cp in settings.counterparties if cp not in acked]


def prepare_dispatch(
    session: Session, trade: Trade, targets: list[str], now: datetime | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """DB half of a (re)send: upsert one SENT row per target. No network here."""
    now = now or utcnow()
    prepared = []
    for cp in targets:
        conf = session.scalar(
            select(Confirmation).where(
                Confirmation.trade_id == trade.id, Confirmation.counterparty == cp
            )
        )
        if conf is None:
            conf = Confirmation(trade_id=trade.id, counterparty=cp, attempt=1)
            session.add(conf)
        else:
            conf.attempt += 1
        conf.report_id = make_report_id(trade.trade_ref, cp, conf.attempt)
        conf.status = ConfStatus.SENT
        conf.sent_at = now
        conf.acked_at = None
        conf.reject_reason = None
        session.flush()
        _publish(session, conf)
        prepared.append((cp, build_tcr(trade, conf)))
    return prepared


def send_all(client: CounterpartyClient, prepared: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Network half: returns counterparties we failed to reach. Their rows stay SENT,
    which is exactly what monitoring later reports as an unacked break."""
    failed = []
    for cp, tcr in prepared:
        try:
            client.send_tcr(cp, tcr)
            log.info("TCR %s sent to %s", tcr["report_id"], cp)
        except Exception as exc:
            log.warning("TCR %s to %s failed: %s", tcr["report_id"], cp, exc)
            failed.append(cp)
    return failed


def dispatch_trade(
    db: Database,
    client: CounterpartyClient,
    settings: Settings,
    trade_id: int,
    only: str | None = None,
) -> list[str]:
    """Send (or resend) a trade's TCRs. Returns the counterparties targeted."""
    with db.session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            raise LookupError(f"unknown trade {trade_id}")
        targets = targets_for(session, settings, trade, only)
        prepared = prepare_dispatch(session, trade, targets)
    # transaction committed here -> now it is safe to talk to the network
    send_all(client, prepared)
    return targets


def handle_ack(
    session: Session, settings: Settings, ack: AckIn, now: datetime | None = None
) -> AckResult:
    now = now or utcnow()
    conf = session.scalar(select(Confirmation).where(Confirmation.report_id == ack.report_id))
    if conf is None:
        return AckResult(matched=False, detail="unknown or superseded report_id; ignored")
    if conf.status != ConfStatus.SENT:
        return AckResult(matched=True, detail=f"duplicate ack ignored (already {conf.status})")

    accepted = ack.status == "ACCEPTED"
    conf.status = ConfStatus.ACKED if accepted else ConfStatus.REJECTED
    conf.acked_at = now
    conf.reject_reason = None if accepted else (ack.reject_reason or "rejected")
    metrics.CONFIRMATIONS.labels(counterparty=conf.counterparty, status=conf.status).inc()
    metrics.ACK_LATENCY.observe(max((now - conf.sent_at).total_seconds(), 0))
    _publish(session, conf)

    trade = session.get(Trade, conf.trade_id)
    assert trade is not None
    if accepted:
        refresh_confirmation_status(session, trade, settings)
    else:
        monitoring.raise_alert(
            session,
            rule="CONFIRMATION_REJECTED",
            severity="HIGH",
            subject=conf.report_id,
            message=f"{trade.trade_ref} rejected by {conf.counterparty}: {conf.reject_reason}",
        )
    return AckResult(matched=True, detail=f"recorded {conf.status}")
