from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from fxlab import booking, monitoring, posttrade
from fxlab.config import Settings
from fxlab.db import Database
from fxlab.models import Alert
from fxlab.pricing import PriceFeed
from fxlab.schemas import AckIn

from .conftest import make_trade
from .fakes import FakeClient

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def rules(db: Database) -> list[str]:
    with db.session() as s:
        return sorted(a.rule for a in s.scalars(select(Alert)))


def test_reconciliation_lists_absent_confirmations_as_not_sent(
    db: Database, settings: Settings
) -> None:
    make_trade(db)  # booked, but nothing was ever dispatched
    with db.session() as s:
        rows = monitoring.reconciliation(s, settings)
    assert [r["status"] for r in rows] == ["NOT_SENT"] * 3
    assert all(r["is_break"] for r in rows)


def test_reconciliation_status_mix_and_breaks_only(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    posttrade.dispatch_trade(db, fake_client, settings, trade.id)
    ids = {cp: t["report_id"] for cp, t in fake_client.sent}
    with db.session() as s:
        posttrade.handle_ack(s, settings, AckIn(report_id=ids["CCP_ALPHA"], status="ACCEPTED"))
        posttrade.handle_ack(
            s, settings, AckIn(report_id=ids["CCP_BRAVO"], status="REJECTED", reject_reason="x")
        )
        everything = monitoring.reconciliation(s, settings)
        breaks = monitoring.reconciliation(s, settings, breaks_only=True)
    assert {r["counterparty"]: r["status"] for r in everything} == {
        "CCP_ALPHA": "ACKED",
        "CCP_BRAVO": "REJECTED",
        "CCP_CHARLIE": "SENT",
    }
    assert {r["counterparty"] for r in breaks} == {"CCP_BRAVO", "CCP_CHARLIE"}
    alpha = next(r for r in everything if r["counterparty"] == "CCP_ALPHA")
    assert alpha["ack_latency_ms"] is not None and not alpha["stuck"]


def test_sweep_alerts_on_unacked_after_sla_and_dedupes(
    db: Database, fake_client: FakeClient, settings: Settings
) -> None:
    trade = make_trade(db)
    posttrade.dispatch_trade(db, fake_client, settings, trade.id)
    later = datetime.now(UTC) + timedelta(seconds=settings.ack_sla_seconds + 1)

    with db.session() as s:
        assert monitoring.sweep_unacked(s, settings, now=datetime.now(UTC)) == []  # inside SLA
        assert len(monitoring.sweep_unacked(s, settings, now=later)) == 3
        assert monitoring.sweep_unacked(s, settings, now=later) == []  # already alerted
    assert rules(db) == ["UNACKED_TOO_LONG"] * 3


def test_sweep_alerts_on_trades_that_were_never_dispatched(
    db: Database, settings: Settings
) -> None:
    make_trade(db)
    later = datetime.now(UTC) + timedelta(seconds=settings.ack_sla_seconds + 1)
    with db.session() as s:
        monitoring.sweep_unacked(s, settings, now=later)
    assert rules(db) == ["NOT_DISPATCHED"] * 3


def test_large_notional_rule(db: Database, feed: PriceFeed, settings: Settings) -> None:
    small = make_trade(db, quantity=Decimal(1_000_000))
    big = make_trade(db, quantity=Decimal(6_000_000))
    with db.session() as s:
        assert monitoring.check_trade(s, settings, feed, small) == []
        [alert] = monitoring.check_trade(s, settings, feed, big)
    assert alert.rule == "LARGE_NOTIONAL" and alert.subject == big.trade_ref


def test_off_market_rule(db: Database, feed: PriceFeed, settings: Settings) -> None:
    fair = make_trade(db, price=Decimal("1.0851"))
    fat_finger = make_trade(db, price=Decimal("1.1050"))  # ~180 bps above the 1.0850 mid
    with db.session() as s:
        assert monitoring.check_trade(s, settings, feed, fair) == []
        [alert] = monitoring.check_trade(s, settings, feed, fat_finger)
    assert alert.rule == "OFF_MARKET_PRICE"


def test_rapid_repeat_rule_fires_on_the_nth_trade(
    db: Database, feed: PriceFeed, settings: Settings
) -> None:
    results = []
    with db.session() as s:
        for _ in range(settings.rapid_repeat_count):
            trade = booking.book_trade(
                s,
                account_id="HEDGE-3",
                symbol="EUR/USD",
                side="SELL",
                quantity=Decimal(100_000),
                price=Decimal("1.0846"),
                now=NOW,
            )
            # Same order as production: check each trade right after it is booked.
            results.append(monitoring.check_trade(s, settings, feed, trade, now=NOW))
    assert all(r == [] for r in results[:-1])
    assert [a.rule for a in results[-1]] == ["RAPID_REPEAT"]


def test_summary_counts(db: Database, fake_client: FakeClient, settings: Settings) -> None:
    trade = make_trade(db)
    posttrade.dispatch_trade(db, fake_client, settings, trade.id)
    with db.session() as s:
        info = monitoring.summary(s, settings)
    assert info["breaks"] == 3
    assert info["trades_by_status"] == {"BOOKED": 1}
    assert info["counterparties"]["CCP_ALPHA"] == {"SENT": 1}
