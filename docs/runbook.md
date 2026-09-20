# Runbook

Production is two systemd services on the shared box `fxlab.alicepage.com` (SSH in as a user allowed to run `sudo`).
**This host runs other people's sites — change only fxlab things.** Locally, run the services in terminals or `docker compose`.

## Is it up?

```bash
systemctl status fxlab-app fxlab-counterparty      # both "active (running)"
curl -fsS http://127.0.0.1:8100/health             # process alive
curl -fsS http://127.0.0.1:8100/ready              # database reachable
curl -fsS -H 'Host: fxlab.alicepage.com' http://127.0.0.1/health   # through nginx
readlink /opt/fxlab/current                        # which release is live (a commit sha)
ls -1t /opt/fxlab/releases                         # kept releases, newest first
```

## Logs

```bash
journalctl -u fxlab-app -f                          # gateway, ticker, sweeper
journalctl -u fxlab-counterparty -f                 # fake counterparties
journalctl -u fxlab-app --since "-1h" | grep -i 'TCR .* failed'        # counterparties we could not reach
journalctl -u fxlab-app -p err --since today        # errors only
sudo tail -f /var/log/nginx/fxlab.access.log /var/log/nginx/fxlab.error.log
```

## Metrics (`/metrics`, localhost only)

```bash
curl -s http://127.0.0.1:8100/metrics | grep '^fxlab_'
#  fxlab_orders_total{status}            fxlab_trades_booked_total{source}
#  fxlab_confirmations_total{counterparty,status}   fxlab_alerts_total{rule}
#  fxlab_ack_latency_seconds_*           fxlab_ws_clients
```

## SQL (dedicated database `fxlab` in the host's PostgreSQL)

```bash
sudo -u postgres psql -d fxlab
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

-- Outcome per counterparty
SELECT counterparty, status, count(*) FROM confirmations GROUP BY 1, 2 ORDER BY 1, 2;

-- Open alerts
SELECT id, severity, rule, message, created_at FROM alerts WHERE status = 'OPEN' ORDER BY id DESC;
```

## Drills

### 1. A counterparty is silent (unacked trades)
*Symptom:* dashboard cells stay `SENT`; `UNACKED_TOO_LONG` alerts after 30 s; `fxlab_confirmations_total` stops growing for one counterparty.
1. `curl -s http://127.0.0.1:8101/agencies` — is that counterparty in mode `SILENT`? (Admin panel, or `PUT /api/admin/counterparties/{code}/mode` with the API key.)
2. Is the service up? `systemctl status fxlab-counterparty`; `sudo systemctl restart fxlab-counterparty`.
3. Fix, then **resend**: dashboard ↻ or `POST /api/posttrade/resend {"trade_id": N}` (only un-acked counterparties are re-sent).
4. Confirm the break cleared: `GET /api/monitoring/reconciliation?breaks_only=true` is empty.

### 2. A counterparty rejects
*Symptom:* `REJECTED` cell, `CONFIRMATION_REJECTED` alert with the reason. Read the reason (hover the tag or the alert), fix the cause (here: mode `REJECT`), resend.

### 3. Acks arrive but nothing updates
`journalctl -u fxlab-counterparty | grep 'could not deliver ack'` — usually a wrong `GATEWAY_ACK_URL`, or an `API_KEY` mismatch (the ack endpoint answers 401). Both services read the same `/opt/fxlab/.env`.

### 4. Database is down
*Symptom:* `/ready` returns 503, orders fail. `systemctl status postgresql@16-main`; `journalctl -u postgresql@16-main`.
The app reconnects by itself (`pool_pre_ping`). **PostgreSQL is shared with other apps on this host — never restart it without telling their owners.** Check disk: `df -h /`.

### 5. The dashboard shows "reconnecting…"
Path: browser → nginx `location /ws` → `127.0.0.1:8100`. Test the app directly on the server (a WebSocket client against `ws://127.0.0.1:8100/ws`); if that works, the problem is nginx (upgrade headers, `proxy_read_timeout`).

### 6. A bad release
`deploy-native.sh` rolls back by itself when `/ready` fails. If it was healthy but wrong: run *Deploy* with the previous tag (fast: symlink flip). Write down what the tests missed.

### 7. Disk filling up
The root disk is small and shared. `df -h /`, `du -sh /opt/fxlab/releases/*` (only the newest 3 are kept), `journalctl --disk-usage`. Don't delete anything outside `/opt/fxlab` — tell the box's owner.

## Linux toolbox

| Need | Command |
|---|---|
| Service state / since when | `systemctl status fxlab-app`, `systemctl show -p ActiveEnterTimestamp fxlab-app` |
| Restart just fxlab | `sudo systemctl restart fxlab-counterparty fxlab-app` |
| Who listens on a port | `sudo ss -ltnp \| grep -E '8100\|8101'` |
| Reach a port | `nc -vz 127.0.0.1 8100` |
| CPU / memory of the app | `systemctl status fxlab-app` (Memory line), `top -p $(systemctl show -p MainPID --value fxlab-app)` |
| Disk | `df -h /`, `du -sh /opt/fxlab` |
| Count & rank events in logs | `journalctl -u fxlab-app --no-pager \| grep -o 'CCP_[A-Z]*' \| sort \| uniq -c \| sort -rn` |
| Follow one trade | `journalctl -u fxlab-app --no-pager \| grep TRD-20260920-000042` |
| UTC time (logs are UTC) | `date -u` |
