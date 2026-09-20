# Runbook

Where to look and which command answers the question. On the server: `cd /opt/fxlab` and use
`docker compose -f compose.prod.yml …` (abbreviated `dc` below). Locally: run the services in terminals / `docker compose`.

```bash
alias dc='docker compose -f compose.prod.yml'
```

## Is it up?

```bash
dc ps                                       # 3 containers: app, counterparty, db — all "running"/"healthy"
curl -fsS http://127.0.0.1:8100/health      # process alive
curl -fsS http://127.0.0.1:8100/ready       # database reachable
curl -fsS https://fxlab.alicepage.com/health   # through nginx
cat .current_tag                            # which release is deployed
```

## Logs

```bash
dc logs -f --tail=100 app                   # gateway, ticker, sweeper
dc logs -f --tail=100 counterparty          # fake counterparties
dc logs app | grep -i 'TCR .* failed'       # counterparties we could not reach
dc logs app | grep -c 'ticker iteration failed'
sudo tail -f /var/log/nginx/fxlab.error.log   # proxy-level problems
```

## Metrics (`/metrics`, localhost only)

```bash
curl -s http://127.0.0.1:8100/metrics | grep '^fxlab_'
#  fxlab_orders_total{status}            fxlab_trades_booked_total{source}
#  fxlab_confirmations_total{counterparty,status}   fxlab_alerts_total{rule}
#  fxlab_ack_latency_seconds_*           fxlab_ws_clients
```

## SQL

```bash
dc exec db psql -U fxlab fxlab
```

```sql
-- Everything that is not a clean ack
SELECT t.trade_ref, c.counterparty, c.status, c.reject_reason, now() - c.sent_at AS age
FROM confirmations c JOIN trades t ON t.id = c.trade_id
WHERE c.status <> 'ACKED' ORDER BY t.id DESC;

-- Absences: trades with NO confirmation row for a counterparty (never sent)
SELECT t.trade_ref, cp.code
FROM trades t
CROSS JOIN (VALUES ('CCP_ALPHA'), ('CCP_BRAVO'), ('CCP_CHARLIE')) AS cp(code)
LEFT JOIN confirmations c ON c.trade_id = t.id AND c.counterparty = cp.code
WHERE c.id IS NULL AND t.status <> 'CANCELLED';

-- Rank counterparties by outcome
SELECT counterparty, status, count(*) FROM confirmations GROUP BY 1, 2 ORDER BY 1, 2;

-- Open alerts
SELECT id, severity, rule, message, created_at FROM alerts WHERE status = 'OPEN' ORDER BY id DESC;
```

## Drills

### 1. A counterparty is silent (unacked trades)
*Symptom:* dashboard cells stay `SENT`; `UNACKED_TOO_LONG` alerts after 30 s; `fxlab_confirmations_total` stops growing for one counterparty.
1. `dc logs counterparty | tail` – is it receiving TCRs? `curl -s localhost:8001/agencies` (locally) shows each mode.
2. Is the mode `SILENT`? (Admin panel, or `PUT /api/admin/counterparties/{code}/mode`.)
3. Is the service up? `dc ps`; `dc restart counterparty`.
4. Fix, then **resend**: dashboard ↻ or `POST /api/posttrade/resend {"trade_id": N}` (only un-acked counterparties are re-sent).
5. Confirm the break cleared: `GET /api/monitoring/reconciliation?breaks_only=true` is empty.

### 2. A counterparty rejects
*Symptom:* `REJECTED` cell, `CONFIRMATION_REJECTED` alert with the reason. Read the reason (hover the tag or the alert), fix the cause (here: mode `REJECT`), resend.

### 3. Acks arrive but nothing updates
Check `dc logs counterparty | grep 'could not deliver ack'` — usually a wrong `GATEWAY_ACK_URL` or an `API_KEY` mismatch between the two services (ack endpoint returns 401).

### 4. Database is down
*Symptom:* `/ready` returns 503, orders fail. `dc ps db`, `dc logs db`, `dc restart db`; the app reconnects on its own (`pool_pre_ping`). Check disk: `df -h`, `docker system df` (read-only; **do not prune** on this shared host).

### 5. The dashboard shows "reconnecting…"
WebSocket path: browser → nginx `location /ws` → app. Test locally on the server with any WS client against `ws://127.0.0.1:8100/ws`; if that works, the problem is nginx (upgrade headers / `proxy_read_timeout`).

### 6. A bad release
`deploy.sh` rolls back automatically. If it was healthy but wrong: run *Deploy* with the previous tag. Write down what the tests missed.

## Linux toolbox

| Need | Command |
|---|---|
| Service state / since when | `dc ps`, `docker inspect -f '{{.State.StartedAt}}' fxlab-app-1` |
| Who listens on a port | `sudo ss -ltnp \| grep 8100` |
| Reach a port | `nc -vz 127.0.0.1 8100` |
| CPU / memory | `docker stats --no-stream`, `free -m`, `uptime` |
| Disk | `df -h`, `du -sh /opt/fxlab` |
| Count & rank events in logs | `dc logs app \| grep -o 'CCP_[A-Z]*' \| sort \| uniq -c \| sort -rn` |
| Follow a specific trade | `dc logs app \| grep TRD-20260920-000042` |
| UTC time (logs are UTC) | `date -u` |
