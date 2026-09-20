# Trainee curriculum

Each milestone is one or two days and walks the trainee through a **whole SDLC loop** on a real
change: requirement → design → ticket → branch → code + tests → PR → CI → review → merge → release → deploy → operate.
The app in `main` is the **reference solution**; milestones ask trainees to *extend* it (or rebuild a
slice from a mentor-prepared starter branch). Every milestone ends with a merged PR and a green pipeline.

**Definition of done (all milestones):** acceptance criteria met · tests added (written first where possible) ·
`make check` green locally · PR approved · CI green · docs updated if behaviour changed.

| # | Milestone | Skills | SDLC stage emphasised |
|---|---|---|---|
| 0 | Onboarding | git, venv, Make, reading a codebase | Setup |
| 1 | Trace a trade | FX flow, FastAPI, SQL | Requirements & analysis |
| 2 | First feature: a new pair | pricing, validation, TDD | Design → implementation |
| 3 | Booking rules | state machines, dates | Implementation & testing |
| 4 | Post-trade resilience | HTTP, retries, idempotency | Design for failure |
| 5 | A new monitoring rule | rules, thresholds, alerts | Feature delivery end-to-end |
| 6 | Real-time | WebSocket, pub/sub | API contracts |
| 7 | Quality gates | coverage, types, pre-commit | Quality |
| 8 | Pipelines | GitHub Actions, Docker | CI/CD |
| 9 | Release & deploy | tags, compose, nginx, rollback | Release & operations |
| 10 | Operate | logs, metrics, SQL, incidents | Support / SRE |
| ★ | Stretch | FIX, migrations, auth, markouts | Depth |

---

## M0 — Onboarding
- Clone, `make venv`, `make check`, run both services, click through the dashboard.
- Read `README.md` and `docs/architecture.md`; draw the flow from memory.
- **Ticket:** fix a typo in the docs via a branch + PR (practise the workflow itself).
- **Done when:** first PR merged; trainee can start the stack unaided.

## M1 — Trace a trade (analysis)
- Send one order. Follow it through: `POST /api/orders` → `services.place_order` → `trading.submit_order`
  → `booking.book_trade` → `posttrade.dispatch_trade` → counterparty → `posttrade.handle_ack`.
- Query the DB directly (SQLite: `sqlite3 fxlab.db`, or Postgres via the runbook) for the trade and its 3 confirmations.
- **Deliverable:** a one-page sequence diagram + list of every table row the order created.
- **Questions to answer:** Why is the TCR sent *after* the commit? What happens if a counterparty is down?

## M2 — First feature: add EUR/GBP (a cross pair)
- **Requirement:** clients can trade EUR/GBP. Notional in USD must be computed for alerts and P&L.
- **Design note (required before coding):** how to convert GBP to USD using the GBP/USD quote (the current code raises for crosses — see `pricing.notional_usd`).
- **Tasks:** add the instrument; extend `notional_usd` / `quote_to_usd` with a rate lookup; tests first.
- **Acceptance:** an EUR/GBP order fills, books, confirms; `LARGE_NOTIONAL` uses the right USD value; positions report USD P&L.
- **Watch for:** `PriceFeed` needs the GBP/USD rate at conversion time — where should that dependency live?

## M3 — Booking rules
- **Ticket A:** business-day calendar with holidays (`booking.add_business_days`); tests for a holiday on T+1.
- **Ticket B:** `POST /api/trades/{id}/cancel` using `booking.transition`; reject cancelling a `SETTLED` trade (409).
- **Ticket C:** average entry price per position.
- **Acceptance:** each has failing-then-passing tests; the state-machine table is the only place transitions are defined.

## M4 — Post-trade resilience
- **Ticket A:** add a counterparty mode `DELAYED` (acks after N seconds). Show that the SLA alert can fire *and then* the ack arrives — what should the alert do?
- **Ticket B:** automatic retry: resend an unacked TCR after 10 s, at most 3 attempts, then stop and alert. Keep it idempotent.
- **Ticket C:** counterparty returns HTTP 500 for a TCR — assert the row stays `SENT` and monitoring reports it.
- **Discussion:** why does a resend need a new `report_id`? Reproduce the late-ack bug by temporarily reusing ids, then restore the fix (`tests/test_posttrade.py::test_late_ack_for_a_superseded_attempt_is_ignored`).

## M5 — A new monitoring rule (full feature loop)
- **Requirement:** flag a **wash pattern** — the same account buys and sells the same pair within 30 s.
- Write the spec (rule, threshold, severity, false-positive cases) → ticket → implement in `monitoring.check_trade` → thresholds in `config.py` → unit test with an injected `now` → document in `architecture.md`.
- **Acceptance:** rule fires once per pattern (dedupe key!), is configurable by env var, shows in the alerts panel.

## M6 — Real-time
- **Ticket A:** add a `positions` topic pushed after each fill; update the dashboard to use it instead of refetching.
- **Ticket B:** the WebSocket client should show "stale" if no tick arrives for 5 s.
- **Discussion:** why does `EventBus.publish` use `call_soon_threadsafe`? (Try removing it and read the failure.)

## M7 — Quality gates
- Install pre-commit (`pre-commit install`). Get coverage on `counterparty_client.py` and `main.py` above 95 %.
- Turn on `disallow_untyped_defs` for `booking.py` and fix what mypy finds.
- **Discussion:** what does 100 % coverage *not* prove? Find a mutation the tests miss.

## M8 — Pipelines
- Read `ci.yml`. Deliberately break each check on a branch (lint error, type error, failing test, coverage drop) and read how CI reports it.
- **Ticket:** add a CI job that fails the PR if `docs/` links are broken, or that builds the image on every PR.
- **Ticket:** make the Postgres job also run the two timer tests.

## M9 — Release & deploy
- Cut a release: bump `pyproject.toml` version, tag `v0.x.y`, watch `deploy.yml` verify, ship and health-check.
- Read `deploy/native/deploy-native.sh` and explain each step: unpack → own venv → symlink flip → restart → health check → rollback.
- Practise a **rollback** with *Actions → Deploy → Run workflow* and an older tag (why is it fast?).
- Practise a **bad deploy** (on a training server, never the shared one): ship a release that fails `/ready` and watch it roll back by itself.
- Read `deploy/nginx-fxlab.conf` and explain why it has no `map` and no `default_server`, and why the app listens on loopback only.
- Follow `docs/sdlc-and-deploy.md`. **The shared server serves other sites — trainees never edit nginx, the firewall or PostgreSQL; a mentor owns those.**

## M10 — Operate
Work through the drills in `docs/runbook.md`: a silent counterparty, a rejecting counterparty, a stopped database,
a full disk, a slow ack. Each ends with a short incident note (timeline, cause, fix, prevention).

## ★ Stretch goals
- **Real FIX:** replace the JSON messages with FIX 4.4 `35=AE`/`35=AR` using QuickFIX or `simplefix`; keep the `CounterpartyClient` interface.
- **Migrations:** introduce Alembic; replace `create_all`.
- **Auth:** per-user login and roles (trader vs. ops vs. admin) instead of one shared key.
- **Markouts:** compute post-trade price drift at +1/+5/+30 s from stored quotes — where do the quotes need to be persisted?
- **Perps / margin:** the earlier `perps-margin-poc` accounting (funding, margin, liquidation) as an add-on module.
- **Observability:** run Prometheus + Grafana in compose against `/metrics`; add an ack-latency panel and an unacked-count alert.
