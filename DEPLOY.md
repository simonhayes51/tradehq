# Deploying to Coolify: one repo, one database

This assumes you've pushed this folder's contents as a new GitHub repo and
are pointing Coolify at it as a docker-compose project (`docker-compose.yml`
is at the root). Nothing here touches the existing Railway deployment.

## 1. Set project-level environment variables in Coolify

Copy `.env.example`, fill in real values, and set them in Coolify's
environment-variables UI for the project (not per-service — they're shared).
`SECRET_KEY` and `JWT_PRIVATE_KEY` are the two the backend refuses to boot
without (`main.py:90`). Generate both with something like
`python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## 2. Before the FIRST boot, ever: bootstrap the legacy schema

**This has to happen before `docker-compose up` touches the backend for
the very first time. Order matters and there is no clean way back if you
get it backwards — read to the end before running anything.**

Twelve of the earliest migrations (subscriptions, trade finder,
monetization, social feed, one 2025 file — everything in `LEGACY_BASELINE`
in `scripts/run_migrations.py`) predate the migration runner. They were
applied by hand against the original database years ago, aren't
idempotent, and boot-time migrations deliberately skip re-running them:
`run_on_boot()` calls `run(dsn, target="core")` with `execute_legacy=False`
(`scripts/run_migrations.py:181`). On the *existing* Railway database
that's correct — those tables already exist.

On a **new, empty** database, "skip" doesn't mean "already applied" — the
runner still `INSERT`s all twelve into `schema_migrations` as done, just
without ever running their `CREATE TABLE`s. And because they're now
recorded as applied, running `--execute-legacy` *afterward* changes
nothing — the runner skips anything already in `schema_migrations`
regardless of the flag. **If the backend boots even once before this step,
those tables never get created, ever, without manually deleting the wrong
rows from `schema_migrations` first.**

So: run this against the empty database, before `docker-compose up`
starts the `backend` service for the first time (bringing up `postgres`
alone first is fine and expected):

```bash
cd backend
pip install -r requirements.txt
DATABASE_URL="postgresql://futhub:<POSTGRES_PASSWORD>@<host>:5432/futhub" \
  python scripts/run_migrations.py --execute-legacy
```

That's the only manual step. It targets `core` (the script's default —
there's no `--target` flag, just `--player` to switch to
`PLAYER_DATABASE_URL`, verified by reading `scripts/run_migrations.py`'s
`argparse` block directly rather than guessing). Neither the `player` nor
`watchlist` targets have any `LEGACY_BASELINE` entries — migrations 016
and 039 are the only watchlist ones and both postdate the runner — so
those need no manual step and are handled entirely by the normal boot-time
path (`RUN_MIGRATIONS_ON_BOOT=1`, on by default) the first time the
backend actually starts.

## 3. Migrations 038 and 040 need auto_sync to have run once

`futgg_market_layer` (038) and `futgg_recommendation_outcomes` (040) both
declare `requires-table: futgg_players, futgg_bin_history,
futgg_sales_history` — tables auto_sync creates itself on first run, not
migrations in this repo. If the backend boots before any auto_sync worker
has run once, it logs `skipping 040_... - waiting on table(s) ...` and
retries on every future boot until they exist. Not an error, just a
sequencing note: bring the `futgg_player_worker` and `futgg_price_worker`
services up at least once before you go looking for why the FUT.GG
recommendation tables don't exist yet.

## 4. Build the frontend with the real public backend URL

`VITE_API_URL` / `VITE_API_BASE_URL` are compiled into the frontend's JS at
`npm run build` time (Vite convention), not read at container start. Set
`PUBLIC_API_URL` in Coolify to wherever you expose the `backend` service
publicly (its assigned domain, not the internal `backend` hostname the
compose network uses) before the *first* frontend build. Changing it later
means rebuilding the frontend service, not just restarting it.

## 5. `docker-compose up`

Bring `postgres` up first (or just `up -d` — `depends_on: condition:
service_healthy` on every other service already waits for it). Everything
else can start together after that.

## What's deliberately not solved here

- **Redis** — `app/auth/api_keys.py` and `app/routers/v2/ai_chat.py` import
  it lazily inside functions, so the backend boots fine without it. Not in
  `docker-compose.yml`. Add a `redis` service yourself if you need whatever
  feature that gates.
- **TLS / domains / reverse proxy** — Coolify handles this at the platform
  level; nothing in this repo assumes a specific setup.
- **The v1 FUTBIN-backed product surface** flagged in `REVIEW.md` — still
  present, still routed, still reading from views that stop updating
  unless `ENABLE_LEGACY_FUTBIN` is set. Moving to a new host doesn't
  resolve that; it's a product decision independent of where this runs.
