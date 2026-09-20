# fxlab

A small, complete FX platform for **training**: price streaming, order entry, trade booking,
post-trade confirmation with counterparties, and transaction monitoring — plus the whole
software-delivery loop around it (tests, CI, containers, CD to a server, runbook).

**Stack:** Python 3.11+, FastAPI, WebSockets, SQLAlchemy (SQLite locally, Postgres in
production), pytest, ruff, mypy, GitHub Actions, systemd + nginx in production (Docker only for local dev). No front-end build step.

## The business flow in one picture

```
 browser ──WS: live quotes, trades, acks, alerts──┐
    │ REST                                        │
    ▼                                             │
┌────────────────────────── fxlab app (FastAPI) ──┴─────────────────────────┐
│ pricing → trading → booking ──► post-trade gateway ──► monitoring/alerts  │
│ (quotes)  (orders)  (trades,     (Trade Capture Reports    (breaks, SLA,  │
│                      positions)   out, acks in, resend)     rules)        │
└──────────────────────────────────────┬────────────────────────────────────┘
              SQL (trades, confirmations)│  HTTP: TCR out ▲ │ ack back (async callback)
                                        ▼                 │ ▼
                                    Postgres        fake counterparties service
                                                    CCP_ALPHA · CCP_BRAVO · CCP_CHARLIE
                                                    (modes: ACK / REJECT / SILENT)
```

1. **Trade** – a client sends a market/limit order; it fills against the streamed bid/ask.
2. **Book** – the fill becomes a trade: `BOOKED → CONFIRMED → SETTLED`, T+2 value date, positions & P&L.
3. **Post-trade** – the trade is reported to three counterparties; each acks or rejects (or stays silent).
4. **Monitor** – a live grid of *every trade × every counterparty*; breaks, SLA breaches and rule alerts, with one-click resend.

## Quick start

```bash
make venv          # creates .venv, installs the app + dev tools
make run-cp        # terminal 1: fake counterparties on :8001
make run           # terminal 2: app on :8000  ->  http://localhost:8000
make check         # lint + types + tests (what CI runs)
```

Or the whole stack (app + Postgres + counterparties) in containers: `make up` → http://localhost:8000.

Things to try (dashboard at `/`, API docs at `/docs`):

1. Send a market order → watch the trade go `BOOKED → CONFIRMED` as three acks arrive.
2. Admin panel: set **CCP_BRAVO → REJECT** and **CCP_CHARLIE → SILENT**, send another order.
   You now have a rejected break and an unacked break; an alert fires when the 30 s SLA passes.
3. Set them back to ACK and press **↻** on the breaks. The trade confirms.
4. Book an off-market trade from the admin panel → `OFF_MARKET_PRICE` alert.

## Repository map

| Path | What it is |
|---|---|
| `fxlab/pricing.py` `trading.py` `booking.py` | quotes · order execution · trade lifecycle & positions |
| `fxlab/posttrade.py` `monitoring.py` | confirmations, acks, resend · reconciliation, alert rules |
| `fxlab/counterparty/` | the separate service that plays the counterparties |
| `fxlab/routes/` `main.py` | thin HTTP/WebSocket layer · app factory & background loops |
| `fxlab/static/` | the dashboard (plain HTML/JS/CSS) |
| `tests/` | unit, API and end-to-end tests |
| `Dockerfile` `docker-compose.yml` | local containers (dev only) |
| `deploy/` | production: native (no Docker) bootstrap + deploy scripts, systemd units, nginx site — see the deploy guide |
| `.github/workflows/` | CI (`ci.yml`) and CD (`deploy.yml`) |
| `docs/` | architecture, curriculum, SDLC & deploy guide, runbook, glossary |

## Documentation

- [docs/architecture.md](docs/architecture.md) – how it works and *why* it is built this way
- [docs/curriculum.md](docs/curriculum.md) – the trainee path: milestones and tickets mapped to SDLC stages
- [docs/sdlc-and-deploy.md](docs/sdlc-and-deploy.md) – branching, CI, releases, server setup, rollback
- [docs/runbook.md](docs/runbook.md) – operating it: symptoms → commands
- [docs/glossary.md](docs/glossary.md) – FX and post-trade terms

## Scope and limits

A teaching sandbox: simulated prices, fake counterparties, no real money, minimal auth
(a shared API key on admin endpoints). Deliberately out of scope: order books/matching,
margin, perps, real FIX sessions, crosses, holiday calendars — several are curriculum stretch goals.
