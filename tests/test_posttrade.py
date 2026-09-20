from __future__ import annotations

from sqlalchemy import select

from fxlab import posttrade
from fxlab.config import Settings
from fxlab.db import Database
from fxlab.models import Alert, Confirmation, ConfStatus, Trade, TradeStatus
from fxlab.schemas import AckIn

from .conftest import make_trade
from .fakes import FakeClient


def ack(report_id: str, status: str = "ACCEPTED", reason: str | None = None) -> AckIn:
    return AckIn(report_id=report_id, status=status, reject_reason=reason)  # type: ignore[arg-type]


def dispatch(
    db: Database, client: FakeClient, settings: Settings, trade_id: int, **kw: str
) -> list[str]:
    return posttrade.dispatch_trade(db, client, settings, trade_id, **kw)


def confirmations(db: Database, trade_id: int) -> dict[str, Confirmation]:
    with db.session() as s:
        rows = s.scalars(select(Confirmation).where(Confirmation.trade_id == trade_id))
        return {c.counterparty: c for c in rows}


def test_dispatch_sends_one_tcr_per_counterparty(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    targets = dispatch(db, fake_client, settings, trade.id)

    assert targets == list(settings.counterparties)
    assert [cp for cp, _ in fake_client.sent] == list(settings.counterparties)
    tcr = fake_client.reports_for("CCP_ALPHA")[0]
    assert tcr["msg_type"] == "TradeCaptureReport"
    assert tcr["report_id"] == f"{trade.trade_ref}-CCP_ALPHA-1"
    assert (tcr["symbol"], tcr["side"], tcr["last_qty"]) == ("EUR/USD", "BUY", "1000000.00")
    assert all(c.status == ConfStatus.SENT for c in confirmations(db, trade.id).values())


def test_trade_is_confirmed_only_when_every_counterparty_acks(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id)
    ids = {cp: t["report_id"] for cp, t in fake_client.sent}

    with db.session() as s:
        for cp in ("CCP_ALPHA", "CCP_BRAVO"):
            assert posttrade.handle_ack(s, settings, ack(ids[cp])).matched
        assert s.get(Trade, trade.id).status == TradeStatus.BOOKED  # type: ignore[union-attr]
        posttrade.handle_ack(s, settings, ack(ids["CCP_CHARLIE"]))
        assert s.get(Trade, trade.id).status == TradeStatus.CONFIRMED  # type: ignore[union-attr]


def test_reject_records_reason_raises_alert_and_blocks_confirmation(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id)
    ids = {cp: t["report_id"] for cp, t in fake_client.sent}

    with db.session() as s:
        posttrade.handle_ack(s, settings, ack(ids["CCP_ALPHA"]))
        posttrade.handle_ack(s, settings, ack(ids["CCP_CHARLIE"]))
        posttrade.handle_ack(s, settings, ack(ids["CCP_BRAVO"], "REJECTED", "bad SSI"))
        assert s.get(Trade, trade.id).status == TradeStatus.BOOKED  # type: ignore[union-attr]
        alerts = s.scalars(select(Alert)).all()
    assert [a.rule for a in alerts] == ["CONFIRMATION_REJECTED"]
    conf = confirmations(db, trade.id)["CCP_BRAVO"]
    assert (conf.status, conf.reject_reason) == (ConfStatus.REJECTED, "bad SSI")


def test_resend_mints_a_new_report_id_and_only_targets_unacked(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id)
    ids = {cp: t["report_id"] for cp, t in fake_client.sent}
    with db.session() as s:
        posttrade.handle_ack(s, settings, ack(ids["CCP_ALPHA"]))
        posttrade.handle_ack(s, settings, ack(ids["CCP_BRAVO"], "REJECTED", "nope"))
    fake_client.sent.clear()

    targets = dispatch(db, fake_client, settings, trade.id)  # no `only` -> everyone not ACKED

    assert targets == ["CCP_BRAVO", "CCP_CHARLIE"]
    assert fake_client.reports_for("CCP_BRAVO")[0]["report_id"] == f"{trade.trade_ref}-CCP_BRAVO-2"
    conf = confirmations(db, trade.id)["CCP_BRAVO"]
    assert (conf.attempt, conf.status, conf.reject_reason) == (2, ConfStatus.SENT, None)


def test_late_ack_for_a_superseded_attempt_is_ignored(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id, only="CCP_ALPHA")
    old_id = fake_client.reports_for("CCP_ALPHA")[0]["report_id"]
    dispatch(db, fake_client, settings, trade.id, only="CCP_ALPHA")  # resend -> attempt 2

    with db.session() as s:
        result = posttrade.handle_ack(s, settings, ack(old_id))
    assert result.matched is False
    assert confirmations(db, trade.id)["CCP_ALPHA"].status == ConfStatus.SENT


def test_duplicate_ack_is_idempotent(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id, only="CCP_ALPHA")
    rid = fake_client.reports_for("CCP_ALPHA")[0]["report_id"]
    with db.session() as s:
        posttrade.handle_ack(s, settings, ack(rid))
        again = posttrade.handle_ack(s, settings, ack(rid, "REJECTED", "flip-flop"))
    assert "duplicate" in again.detail
    assert confirmations(db, trade.id)["CCP_ALPHA"].status == ConfStatus.ACKED


def test_unreachable_counterparty_leaves_row_sent(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    fake_client.unreachable = {"CCP_CHARLIE"}
    trade = make_trade(db)
    dispatch(db, fake_client, settings, trade.id)  # must not raise

    assert {cp for cp, _ in fake_client.sent} == {"CCP_ALPHA", "CCP_BRAVO"}
    assert confirmations(db, trade.id)["CCP_CHARLIE"].status == ConfStatus.SENT
