#!/usr/bin/env bash
# ONE-TIME server bootstrap for the native (no Docker) deployment. Run as root. Idempotent.
#
# Everything here is ADDITIVE and namespaced to "fxlab". It does not touch nginx, the firewall,
# other systemd units, or any existing Postgres role/database.
#
#   creates: system user fxlab · /opt/fxlab · Postgres role+db "fxlab" · /opt/fxlab/.env (0600)
#            · two systemd units (enabled, not started - deploy-native.sh starts them)
#   installs: python3-venv (only if missing)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE=/opt/fxlab
log() { echo "[install] $*"; }

# --- system user + directories -------------------------------------------------------------
if ! id fxlab >/dev/null 2>&1; then
  useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin fxlab
  log "created system user fxlab"
fi
install -d -m 755 "$BASE" "$BASE/releases" "$BASE/bin"

# --- python venv support (Ubuntu ships python3 without ensurepip) ----------------------------
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  log "installing python3-venv"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv >/dev/null
fi

# --- database: dedicated role + db on the host's existing PostgreSQL --------------------------
if [ ! -f "$BASE/.env" ]; then
  DB_PASSWORD="$(openssl rand -hex 24)"
  API_KEY="$(openssl rand -hex 24)"
  ( umask 077
    cat > "$BASE/.env" <<ENV
DATABASE_URL=postgresql+psycopg://fxlab:${DB_PASSWORD}@127.0.0.1:5432/fxlab
API_KEY=${API_KEY}
COUNTERPARTY_URL=http://127.0.0.1:8101
GATEWAY_ACK_URL=http://127.0.0.1:8100/api/posttrade/acks
ENV
  )
  chown root:root "$BASE/.env"; chmod 600 "$BASE/.env"
  ROLE_PASSWORD="$DB_PASSWORD"
  log "wrote $BASE/.env (0600, root only)"
else
  # Re-run: recover the DB password from the existing file so a half-finished install can resume.
  ROLE_PASSWORD="$(sed -n 's#^DATABASE_URL=postgresql+psycopg://fxlab:\([^@]*\)@.*#\1#p' "$BASE/.env")"
  log "$BASE/.env already exists - leaving it alone"
fi

psql_pg() { su postgres -c "cd /tmp && psql -v ON_ERROR_STOP=1 -tA $*"; }
if [ "$(psql_pg "-c \"SELECT 1 FROM pg_roles WHERE rolname='fxlab'\"")" != "1" ]; then
  [ -n "$ROLE_PASSWORD" ] || { echo "cannot read DB password from $BASE/.env; fix by hand"; exit 1; }
  psql_pg "-c \"CREATE ROLE fxlab LOGIN PASSWORD '${ROLE_PASSWORD}'\"" >/dev/null
  log "created postgres role fxlab"
fi
if [ "$(psql_pg "-c \"SELECT 1 FROM pg_database WHERE datname='fxlab'\"")" != "1" ]; then
  psql_pg "-c \"CREATE DATABASE fxlab OWNER fxlab\"" >/dev/null
  log "created postgres database fxlab"
fi

# --- helper scripts + systemd units -------------------------------------------------------------
install -m 755 "$HERE/deploy-native.sh" "$BASE/bin/deploy-native.sh"
install -m 644 "$HERE/fxlab-app.service" "$HERE/fxlab-counterparty.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable fxlab-counterparty.service fxlab-app.service >/dev/null 2>&1
log "installed + enabled systemd units (not started yet)"
log "done. Next: ship a release with deploy-native.sh"
