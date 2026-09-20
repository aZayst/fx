# SDLC, CI/CD and deployment

## The loop

```
idea → ticket → branch → code + tests → make check → PR → CI → review → merge to main
     → tag vX.Y.Z → verify → (approval) → ship → health check → monitor → (rollback if needed)
```

- **Tickets** use `.github/ISSUE_TEMPLATE/ticket.md` (context, acceptance criteria, out of scope).
- **Branches:** `feat/<short-name>`, `fix/<short-name>`; `main` is always releasable and protected.
- **Commits:** small, imperative subject ("Add EUR/GBP instrument"); explain *why* in the body.
- **PRs:** use the template; link the ticket; CI must be green; at least one review.
- **Local gate:** `make check` (ruff, ruff format, mypy, pytest). Optional `pre-commit install` runs the linters on commit.

Recommended GitHub settings (Settings → Branches → rule for `main`): require a PR, require the
`test`, `test-postgres` and `docker` checks, require one approval, block force-pushes.

## Two environments, two shapes

| | Local / dev | Production (`fxlab.alicepage.com`) |
|---|---|---|
| Runs as | `make run` (+ `make run-cp`), or `docker compose up` | **systemd services, no Docker** |
| Database | SQLite (or Postgres in compose) | the host's **PostgreSQL 16**, dedicated `fxlab` role + database |
| Web | `localhost:8000` | existing **nginx** → `127.0.0.1:8100` |

Docker is for developers only (`Dockerfile`, `docker-compose.yml`, the `docker` CI job). **Nothing in production uses it.**

## CI — `.github/workflows/ci.yml`

| Job | Proves |
|---|---|
| `test` (py3.11, py3.12) | lint, format, types, tests, coverage ≥ 85 % |
| `test-postgres` | the same tests pass on **Postgres**, the production database |
| `docker` | the dev image builds and answers `/ready` |

## CD — `.github/workflows/deploy.yml`

Trigger: push a tag `v*`, or *Actions → Deploy → Run workflow* with a ref.

1. **verify** – lint, types and tests on the exact commit.
2. **deploy** (environment `production` — add *required reviewers* for a manual approval gate):
   `git archive` the commit and stream it over SSH to `deploy-native.sh <sha>` on the server.
3. `deploy-native.sh` builds `/opt/fxlab/releases/<sha>/` (own venv), flips the `current` symlink,
   restarts **only** `fxlab-app` and `fxlab-counterparty`, waits for `/ready`, and **rolls the symlink back
   automatically** if it doesn't pass within 60 s. The newest 3 releases are kept.
4. Optional public check (set the repo variable `PUBLIC_URL` once DNS/TLS are ready).

### GitHub secrets (Settings → Secrets and variables → Actions → environment `production`)

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | `fxlab.alicepage.com` |
| `DEPLOY_USER` | the SSH user (must be able to run `sudo -n /opt/fxlab/bin/deploy-native.sh`) |
| `DEPLOY_SSH_KEY` | private key of a key pair used *only* for deploys |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 fxlab.alicepage.com` output, checked against the server's real fingerprint |

Prefer a dedicated non-root deploy user with a narrow sudoers rule over root:
`deployer ALL=(root) NOPASSWD: /opt/fxlab/bin/deploy-native.sh`.

## Sharing the server safely

The box **already serves other sites** (nginx, a shared PostgreSQL, other systemd apps). Everything fxlab adds is additive and namespaced:

| Rule | How it is enforced |
|---|---|
| Never touch shared nginx config | one new standalone site file; no `http`-level directives. The box's `conf.d/websocket.conf` already defines `$connection_upgrade`, so redefining it would break `nginx -t` — our file writes the upgrade headers literally |
| Never steal another site's traffic | specific `server_name`, no `default_server` |
| Never restart nginx | `nginx -t` then `systemctl reload nginx` |
| No port clashes | app on `127.0.0.1:8100`, counterparties on `127.0.0.1:8101` — loopback only; ports were checked free first |
| Own identity | dedicated `fxlab` system user, `nologin`; units run hardened (`NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, …) |
| Don't share data | dedicated Postgres role + database `fxlab`; other databases are never touched |
| Secrets | `/opt/fxlab/.env`, `root:root 0600`; systemd injects it, the app user can't read the file |
| Bounded footprint | one uvicorn worker per service, ~90 MB RSS; ≤ 3 kept releases (~150 MB each) — the root disk is tight |
| Pipeline never touches nginx/Postgres/firewall | `deploy-native.sh` only writes under `/opt/fxlab` and restarts the two fxlab units |

## One-time server setup (done once by a human)

```bash
# 0. LOOK FIRST. Change nothing yet.
sudo nginx -T | grep -E 'server_name|listen'   # existing sites
sudo ss -ltnp                                   # 8100 / 8101 free?
sudo ufw status verbose; df -h /; systemctl list-units --type=service --state=running

# 1. Bootstrap (idempotent): user, /opt/fxlab, Postgres role+db, .env, systemd units.
#    Installs python3-venv if it is missing; nothing else.
tar -czf - -C deploy native | ssh root@HOST 'mkdir -p /tmp/fxlab-install && tar -xzf - -C /tmp/fxlab-install \
    && bash /tmp/fxlab-install/native/install-native.sh'

# 2. First release
git archive --format=tar.gz HEAD | ssh root@HOST "/opt/fxlab/bin/deploy-native.sh $(git rev-parse --short=8 HEAD)"
ssh root@HOST 'curl -fsS http://127.0.0.1:8100/ready'          # works BEFORE nginx is involved

# 3. nginx site (a human; see the header of deploy/nginx-fxlab.conf)
sudo cp deploy/nginx-fxlab.conf /etc/nginx/sites-available/fxlab.alicepage.com.conf
sudo ln -s /etc/nginx/sites-available/fxlab.alicepage.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx                   # if -t fails: remove the symlink, nothing changed

# 4. DNS: A record fxlab.alicepage.com -> the server  (done)
# 5. HTTPS: open 80/tcp, then certbot --nginx — see "HTTPS (done)" below
```

The dashboard needs no key: the Admin panel (counterparty modes, demo actions) is open by design — it only touches fake data. The only secret is `API_KEY` in `/opt/fxlab/.env`, which the counterparty service sends with each ack so the public internet cannot forge acks; nobody types it.

### HTTPS (done)

`https://fxlab.alicepage.com` uses a Let's Encrypt certificate issued with the box's existing certbot account:

```bash
sudo ufw allow 80/tcp        # HTTP-01 validation and the http->https redirect need port 80
sudo certbot --nginx -d fxlab.alicepage.com --redirect
```

- certbot rewrote **only** `/etc/nginx/sites-available/fxlab.alicepage.com.conf` (adds the 443 block, cert paths and the
  redirect). `deploy/nginx-fxlab.conf` in this repo is the *pre-TLS template*; the server's copy is the certbot-managed one.
- Renewal is automatic (`certbot.timer`). `sudo certbot renew --dry-run` succeeds for every certificate on the box.
- Opening port 80 is also what lets the *other* sites' certificates renew (they use the same HTTP-01 method).
- If the firewall is ever locked down again, renewals — for all sites — will start failing ~30 days before expiry.

Set the repo variable `PUBLIC_URL=https://fxlab.alicepage.com` so the deploy workflow also checks the site from the outside.

## Rollback

- **Automatic:** a release that fails `/ready` within 60 s is rolled back by `deploy-native.sh`.
- **Manual, fast:** *Actions → Deploy → Run workflow → ref = an older tag* (re-activates the kept build: symlink + restart).
  On the server: `/opt/fxlab/bin/deploy-native.sh <sha-of-a-kept-release>` (`ls /opt/fxlab/releases`; stdin unused).
- **Data:** the Postgres database is untouched by deploys. Schema changes need care: today the app only calls `create_all()`
  (adds missing tables, never alters existing ones) — see the migrations stretch goal.

## What has and hasn't been exercised

Run for real on the server: the bootstrap, release deploys, re-activation (rollback) and an automatic rollback of a deliberately
broken release, both services with the ack secret, the nginx site and TLS (before/after comparison of every other site: identical),
`wss://` through nginx, and an order placed through the public URL. The **GitHub Actions workflows themselves have not run yet**
(secrets aren't configured), so the first tag push is their real test — do it with a mentor watching.
