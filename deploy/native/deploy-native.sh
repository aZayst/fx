#!/usr/bin/env bash
# Install ONE release on the server and switch to it. Run as root (or via a narrow sudo rule).
#
#   git archive --format=tar.gz HEAD | ssh root@host /opt/fxlab/bin/deploy-native.sh <release-id>
#
# The release id is the commit sha, so it is immutable. If that release already exists on the
# server the script just RE-ACTIVATES it (no rebuild): that is the fast rollback path.
#
# Layout:  /opt/fxlab/releases/<id>/{fxlab/,venv/,...}   /opt/fxlab/current -> releases/<id>
# Safe by construction: builds the new release NEXT to the running one, switches a symlink,
# restarts only the two fxlab units, and rolls the symlink back if /ready does not pass.
# It never touches nginx or anything outside /opt/fxlab and the two fxlab services.
set -euo pipefail

ID="${1:?usage: deploy-native.sh <release-id>   (tar.gz of the source on stdin)}"
[[ "$ID" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid release id: $ID"; exit 1; }
BASE=/opt/fxlab
REL="$BASE/releases/$ID"
KEEP=3
log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

[ -f "$BASE/.env" ] || { echo "missing $BASE/.env - run install-native.sh first"; exit 1; }

PREVIOUS="$(readlink "$BASE/current" 2>/dev/null || true)"

if [ -d "$REL" ] && [ -x "$REL/venv/bin/python" ]; then
  log "release $ID already built - re-activating it (rollback / redeploy)"
  [ -t 0 ] || cat > /dev/null            # drain the archive the client is still sending
else
  rm -rf "$REL"                          # discard a half-built leftover from a failed attempt
  log "unpacking $ID"
  mkdir -p "$REL"
  tar -xzf - -C "$REL"
  log "building venv + installing (this is the slow part)"
  python3 -m venv "$REL/venv"
  "$REL/venv/bin/python" -m pip install --quiet --no-cache-dir "$REL"
  rm -rf "$REL/build"                    # pip's scratch dir
  chown -R root:root "$REL"; chmod -R a+rX "$REL"
fi

port_ready() { curl -fsS --max-time 3 http://127.0.0.1:8100/ready >/dev/null 2>&1; }
wait_ready() { for _ in $(seq 1 30); do port_ready && return 0; sleep 2; done; return 1; }
switch_to() {
  ln -sfn "$1" "$BASE/current.new" && mv -Tf "$BASE/current.new" "$BASE/current"
  systemctl restart fxlab-counterparty.service fxlab-app.service
}

touch "$REL"                             # retention below is by mtime: activated = recent
log "switching current -> $REL"
switch_to "$REL"

if wait_ready; then
  log "healthy: release $ID is live"
  # keep the newest $KEEP releases (by mtime), never the live one; only inside releases/
  ls -1dt "$BASE"/releases/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
    [ "$(readlink -f "$BASE/current")" = "$(readlink -f "$old")" ] || { log "pruning $old"; rm -rf "$old"; }
  done
  exit 0
fi

log "NOT healthy after 60s. Recent logs:"
journalctl -u fxlab-app.service -n 30 --no-pager || true
if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
  log "rolling back to $PREVIOUS"
  switch_to "$PREVIOUS"
  wait_ready && log "rollback is healthy" || log "rollback is ALSO unhealthy - investigate"
else
  log "no previous release to roll back to"
fi
exit 1
