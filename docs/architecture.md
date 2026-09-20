# Architecture

## Shape: a modular monolith plus one fake-world service

```
fxlab.main (FastAPI app)                          fxlab.counterparty.app (FastAPI, separate process)
 ├─ routes/*         HTTP + WebSocket, thin        └─ POST /tcr/{code}   receive a Trade Capture Report
 ├─ services.py      use-cases spanning modules       (returns 202, answers later)
 ├─ pricing.py       instruments, PriceFeed        └─ per-agency mode: ACK | REJECT | SILENT
 ├─ trading.py       validate + execute orders     └─ POSTs the ack back to /api/posttrade/acks
 ├─ booking.py       Trade, state machine, positions
 ├─ posttrade.py     confirmations, acks, resend
 ├─ monitoring.py    reconciliation + alert rules
 ├─ events.py        in-process pub/sub → WebSocket
 └─ db.py models.py  SQLAlchemy, "publish after commit"
```

Business logic lives in plain functions that take a `Session` (`trading`, `booking`,
`posttrade`, `monitoring`). Routes only translate HTTP ↔ those functions. That is why most
tests need no HTTP at all.

## Lifecycles

**Order:** `OPEN` (resting limit) → `FILLED` | `CANCELLED`; or `REJECTED` (size/notional limits — stored, so it is auditable).
**Trade:** `BOOKED` → `CONFIRMED` (all counterparties acked) → `SETTLED` (value date reached); `CANCELLED` from BOOKED/CONFIRMED.
The allowed moves are a table in `booking.py` (`ALLOWED`); anything else raises `InvalidTransition`.
**Confirmation** (one row per trade × counterparty): `SENT` → `ACKED` | `REJECTED`. `NOT_SENT` is never stored:
reconciliation reports it when the row is *absent*.

## Post-trade message flow

JSON stand-ins for FIX `35=AE` (Trade Capture Report) and `35=AR` (Ack):

```
gateway ── POST /tcr/CCP_ALPHA  {report_id, trade_ref, symbol, side, last_qty, last_px, ...} ──▶ counterparty
gateway ◀── POST /api/posttrade/acks  {report_id, status: ACCEPTED|REJECTED, reject_reason} ── counterparty
```

`report_id` = `<trade_ref>-<counterparty>-<attempt>`. Every (re)send bumps `attempt`, so a **late
ack for an old attempt no longer matches any row and is ignored**; a duplicate ack is idempotent.

## Design decisions (and the alternatives we rejected)

| Decision | Why | Alternative |
|---|---|---|
| **JSON over HTTP** instead of FIX/QuickFIX | Same concepts (TCR, ack, resend, unique report id) without C++ wheels, session config or sequence-number state. Runs in CI and Docker with `pip install`. | Real FIX 4.4 — a good stretch module (see curriculum M10). |
| **Sync SQLAlchemy + sync route handlers** | FastAPI runs them in a thread pool; code stays straightforward for beginners. Only the WebSocket and the two timers are `async`. | Fully async DB stack — more moving parts. |
| **Events are published after commit** (`db.queue_event`) | The dashboard re-reads the API on every event. Publishing before commit would show stale data. Rolled-back work publishes nothing. | Publish inline — racy. |
| **Commit before sending TCRs** (`posttrade.dispatch_trade`) | A fast ack can never arrive for a row that isn't committed yet, and a slow counterparty never holds a DB transaction open. | Send inside the transaction. |
| **Send failures leave the row `SENT`** | That *is* the break: monitoring reports it and the SLA sweeper alerts. Failure is visible, not swallowed. | Raise to the trader — wrong owner for the problem. |
| **`Decimal` + `NUMERIC` for money** | Floats cannot represent 1.0854 exactly. (SQLite stores them as floats — a known limit of the local default; Postgres is exact. CI runs both.) | `float` — never for money. |
| **Counterparty client is a Protocol** (`counterparty_client.py`) | Tests inject a fake that records messages: no network, deterministic. | Mock `httpx` globally. |
| **Dashboard is plain JS** | No Node toolchain to install, build or deploy. Trainees can read every line. | React/Vite — a fine follow-up exercise. |

## Monitoring

**Reconciliation** (`monitoring.reconciliation`): enumerate *what should exist* (each recent trade ×
each counterparty), then look up what does. A missing row is `NOT_SENT` — an absence is a break too.

**Alert rules** (each dedupes on rule + subject, so re-running is harmless):

| Rule | Fires when | Default |
|---|---|---|
| `LARGE_NOTIONAL` | trade notional ≥ threshold | USD 5,000,000 |
| `OFF_MARKET_PRICE` | trade price differs from current mid by more than N bps | 10 bps |
| `RAPID_REPEAT` | same account + pair + side, N trades in a window | 5 in 60 s |
| `UNACKED_TOO_LONG` | confirmation still `SENT` past the SLA (timer sweeper) | 30 s |
| `NOT_DISPATCHED` | trade has no confirmation row for a counterparty past the SLA | 30 s |
| `CONFIRMATION_REJECTED` | a counterparty rejects | immediate |

Thresholds come from `fxlab/config.py` / environment variables.

## Real-time updates

`EventBus.publish()` is safe to call from any thread. Each WebSocket owns a queue; the bus hops
events onto the event loop with `call_soon_threadsafe`. `ws://host/ws?topics=trade,alert` filters
by topic (`quote`, `order`, `trade`, `confirmation`, `alert`). The first message is a `snapshot` of quotes.

## Background loops (`main.py`)

- **ticker** – every second: move prices, push quotes, fill resting limit orders, dispatch their TCRs.
- **sweeper** – every 5 s: raise SLA alerts for confirmations that never got an answer.

## Where this came from

fxlab consolidates two earlier prototypes:

- **`post`** (FX post-trade sandbox: QuickFIX, Postgres, FIFO pipe, Node dashboard) → became
  `posttrade.py`, `monitoring.py`, the counterparty service and the dashboard. Kept: TCR/ack,
  unique-per-send report ids, the trade × counterparty reconciliation idea, resend. Dropped:
  QuickFIX, the named pipe, the Node/TS service, the TUI, `exctl`.
- **`perps-margin-poc`** (perps, spot margin, order book) → intentionally *not* carried over; FX
  booking and post-trade is the training target. The margin/funding maths is a candidate advanced module.

## Known limitations

USD pairs only (no crosses); weekends-only value-date calendar; no real authentication (one shared
API key); prices are a random walk; one process, so no horizontal scaling; the event bus is in-memory.
