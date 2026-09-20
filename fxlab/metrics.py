"""Prometheus metrics, exposed at /metrics. Defined once at import time."""

from prometheus_client import Counter, Gauge, Histogram

ORDERS = Counter("fxlab_orders_total", "Orders received", ["status"])
TRADES_BOOKED = Counter("fxlab_trades_booked_total", "Trades booked", ["source"])
CONFIRMATIONS = Counter(
    "fxlab_confirmations_total", "Confirmation outcomes", ["counterparty", "status"]
)
ACK_LATENCY = Histogram(
    "fxlab_ack_latency_seconds",
    "Time from Trade Capture Report sent to ack received",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
ALERTS = Counter("fxlab_alerts_total", "Alerts raised", ["rule"])
WS_CLIENTS = Gauge("fxlab_ws_clients", "Connected WebSocket clients")
