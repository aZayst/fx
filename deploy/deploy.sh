#!/usr/bin/env bash
# Runs ON THE SERVER (called by the CD pipeline over SSH, or by hand).
#
#   ./deploy.sh <image-tag>        deploy that tag; roll back automatically if it is unhealthy
#
# What it deliberately does NOT do:
#   * touch nginx, systemd, firewall rules or anything outside this directory's compose project;
#   * run `docker system prune`, `docker compose down -v`, or anything that could remove
#     other projects' containers, images or volumes.
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:?usage: ./deploy.sh <image-tag>}"
[ -f .env ] || { echo "missing .env (copy .env.example and fill in the secrets)"; exit 1; }
set -a; . ./.env; set +a
APP_PORT="${APP_PORT:-8100}"
COMPOSE=(docker compose -f compose.prod.yml)

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

# --- preflight: never fight another service for the port ---------------------------------
# (captured into variables rather than `| grep -q`, which can trip `pipefail` via SIGPIPE)
listening="$(ss -ltn "sport = :${APP_PORT}" | tail -n +2)"
if [ -n "$listening" ]; then
  ours="$("${COMPOSE[@]}" ps --status running --quiet app 2>/dev/null || true)"
  if [ -z "$ours" ]; then
    echo "port ${APP_PORT} is already in use by something that is not this stack."
    echo "Pick another APP_PORT in .env (and update proxy_pass in the nginx site), then retry."
    exit 1
  fi
fi

PREVIOUS="$(cat .current_tag 2>/dev/null || true)"

wait_healthy() {
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/ready" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

deploy_tag() {
  export IMAGE_TAG="$1"
  "${COMPOSE[@]}" pull app counterparty
  "${COMPOSE[@]}" up -d
}

log "deploying ${IMAGE}:${TAG} (previous: ${PREVIOUS:-none})"
deploy_tag "$TAG"

if wait_healthy; then
  echo "$TAG" > .current_tag
  log "healthy - deploy of ${TAG} complete"
  # Remove only OLD fxlab images (label-scoped) - never other projects' images.
  docker image prune -f --filter "label=org.opencontainers.image.title=fxlab" >/dev/null || true
  exit 0
fi

log "NOT healthy after 60s. Recent app logs:"
"${COMPOSE[@]}" logs --tail=40 app || true
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$TAG" ]; then
  log "rolling back to ${PREVIOUS}"
  deploy_tag "$PREVIOUS"
  wait_healthy && log "rollback to ${PREVIOUS} is healthy" || log "rollback is ALSO unhealthy - investigate"
fi
exit 1
