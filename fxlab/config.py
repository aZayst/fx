"""Settings, read once from environment variables.

Tests build a `Settings(...)` directly instead of touching the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./fxlab.db"
    api_key: str = ""  # empty = auth disabled (dev only)

    # Post-trade wiring
    counterparty_url: str = "http://localhost:8001"
    gateway_ack_url: str = "http://localhost:8000/api/posttrade/acks"
    counterparties: tuple[str, ...] = ("CCP_ALPHA", "CCP_BRAVO", "CCP_CHARLIE")

    # Pre-trade limits
    min_order_qty: Decimal = Decimal("1000")
    max_order_qty: Decimal = Decimal("10000000")
    max_order_notional_usd: Decimal = Decimal("20000000")

    # Monitoring thresholds
    ack_sla_seconds: int = 30
    large_notional_usd: Decimal = Decimal("5000000")
    off_market_bps: Decimal = Decimal("10")
    rapid_repeat_count: int = 5
    rapid_repeat_window_seconds: int = 60

    # Background loops
    background_tasks: bool = True
    tick_interval_seconds: float = 1.0
    sweep_interval_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            database_url=_env("DATABASE_URL", d.database_url),
            api_key=_env("API_KEY", d.api_key),
            counterparty_url=_env("COUNTERPARTY_URL", d.counterparty_url),
            gateway_ack_url=_env("GATEWAY_ACK_URL", d.gateway_ack_url),
            ack_sla_seconds=int(_env("ACK_SLA_SECONDS", str(d.ack_sla_seconds))),
            large_notional_usd=Decimal(_env("LARGE_NOTIONAL_USD", str(d.large_notional_usd))),
            off_market_bps=Decimal(_env("OFF_MARKET_BPS", str(d.off_market_bps))),
        )
