# SDLC, CI/CD and deployment

## The loop

```
idea → ticket → branch → code + tests → make check → PR → CI → review → merge to main
     → tag vX.Y.Z → build image → (approval) → deploy → verify → monitor → (rollback if needed)
```

- **Tickets** use `.github/ISSUE_TEMPLATE/ticket.md` (context, acceptance criteria, out of scope).
- **Branches:** `feat/<short-name>`, `fix/<short-name>`; `main` is always releasable and protected.
- **Commits:** small, imperative subject ("Add EUR/GBP instrument"); explain *why* in the body.
- **PRs:** use the template; link the ticket; CI must be green; at least one review.
- **Local gate:** `make check` (ruff, ruff format, mypy, pytest). Optional `pre-commit install` runs the linters on commit.

Recommended GitHub settings (Settings → Branches → rule for `main`): require a PR, require the
`test`, `test-postgres` and `docker` checks, require one approval, block force-pushes.

## CI — `.github/workflows/ci.yml`

| Job | Proves |
|---|---|
| `test` (py3.11, py3.12) | lint, format, types, tests, coverage ≥ 85 % |
| `test-postgres` | the same tests pass on **Postgres**, the production database |
| `docker` | the image builds and the container answers `/ready` |

## CD — `.github/workflows/deploy.yml`

Trigger: push a tag `v*` (or *Run workflow* with a tag to redeploy / roll back).

1. Build the image, push to `ghcr.io/azayst/fx:<tag>`.
2. **`production` environment** — configure *required reviewers* in GitHub for a manual approval gate.
3. Copy `deploy/compose.prod.yml` and `deploy/deploy.sh` to `/opt/fxlab` on the server over SSH.
4. Run `deploy.sh <tag>` there: pull, `docker compose up -d`, wait for `/ready`, **auto-rollback** to the previous tag if unhealthy.
5. Verify `https://fxlab.alicepage.com/health` from the runner.

### GitHub secrets (Settings → Secrets and variables → Actions → environment `production`)

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | `fxlab.alicepage.com` |
| `DEPLOY_USER` | the unprivileged deploy user (below) |
| `DEPLOY_SSH_KEY` | private key of a key pair used *only* for deploys |
| `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 fxlab.alicepage.com`, checked against the server's real fingerprint |

Optional variable `PUBLIC_URL` (defaults to `https://fxlab.alicepage.com`) — set it to `http://…` until TLS exists.

## Sharing the server safely

`fxlab.alicepage.com` **already runs nginx for other sites.** The deployment is designed to coexist with it:

| Rule | How it is enforced |
|---|---|
| Never bind ports 80/443 | `compose.prod.yml` publishes only `127.0.0.1:8100`; nginx proxies to it |
| Never edit the main nginx config | our site is one standalone `server` block in its own file, no `http`-level directives (`map`, `upstream`, …) that could collide |
| Never steal another site's traffic | specific `server_name`, no `default_server` |
| Never restart nginx | install with `nginx -t` then `systemctl reload nginx` (graceful) |
| Don't collide with other containers/volumes | compose project `name: fxlab` namespaces everything (`fxlab-app-1`, volume `fxlab_pgdata`) |
| Don't fight for a port | `deploy.sh` refuses to start if `APP_PORT` is taken by something else |
| Don't clean up other people's things | no `docker system prune`, no `down -v`; image pruning is label-scoped to `fxlab` |
| Pipeline never touches nginx | `deploy.sh` and the workflow contain no nginx commands at all |

## One-time server setup (done by a human, not by the pipeline)

Do these **in order**, and read the output of each step before continuing. Steps 1–3 change nothing that nginx uses.

```bash
# 0. LOOK FIRST. Learn what is already there; change nothing yet.
sudo nginx -T | grep -E 'server_name|listen'      # existing sites and ports
sudo ss -ltnp                                     # who owns which port (is 8100 free?)
docker ps; docker --version; docker compose version

# 1. A deploy user (in the docker group) and the app directory
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -o deploy -g deploy -m 750 /opt/fxlab
#    add the PUBLIC half of DEPLOY_SSH_KEY to /home/deploy/.ssh/authorized_keys

# 2. Secrets file
sudo -u deploy cp deploy/.env.example /opt/fxlab/.env   # then edit it
sudo chmod 600 /opt/fxlab/.env                          # set DB_PASSWORD and API_KEY (long random)
#    if 8100 is taken, choose another APP_PORT here AND in nginx-fxlab.conf

# 3. DNS: an A/AAAA record for fxlab.alicepage.com -> this server

# 4. First deploy: push a tag (or run the workflow). Check locally on the server:
curl -fsS http://127.0.0.1:8100/ready              # app works BEFORE nginx is involved

# 5. ONLY NOW add the nginx site
sudo cp deploy/nginx-fxlab.conf /etc/nginx/conf.d/fxlab.conf   # match how the other sites are laid out
sudo nginx -t                                       # must print "test is successful"
sudo systemctl reload nginx                         # reload, NOT restart
#    if nginx -t fails:  sudo rm /etc/nginx/conf.d/fxlab.conf   -> back to exactly how it was

# 6. HTTPS: do it the way the other sites do (e.g. certbot --nginx -d fxlab.alicepage.com),
#    then re-run `sudo nginx -t && sudo systemctl reload nginx`.
```

## Rollback

- **Automatic:** `deploy.sh` rolls back to the previous tag when `/ready` doesn't pass within 60 s.
- **Manual:** *Actions → Deploy → Run workflow → tag = the last good version*. Or on the server:
  `cd /opt/fxlab && ./deploy.sh v0.1.0`.
- **Data:** the Postgres volume `fxlab_pgdata` is untouched by deploys. Schema changes need care: today the app only
  calls `create_all()` (adds missing tables, never alters existing ones) — see the migrations stretch goal.

## Untested-by-the-author note

The workflows, Dockerfile and deploy script have been validated statically (`actionlint`, YAML and `bash -n`) and the app
itself was run against real Postgres, but the container build, GitHub runs and the server deploy have **not** been
executed yet — the first run of CI/CD is the real test. Do the first deploy with a mentor watching.
