# Deploying `auto_sync` on Coolify

Three independent Dockerfile-based Coolify resources, all built from this
same `/auto_sync` base directory, each with its own dedicated Dockerfile.
None of them expose a port or need a domain - they only ever talk
outbound (to fut.gg / api-fc26.easysbc.io) and to `DATABASE_URL`.

All three point `DATABASE_URL` at the **same single Postgres database**
used by `backend` - there is no separate auto_sync database. Do not
create a new Postgres resource for these; use the connection string of
the Postgres instance already backing the rest of the deployment.

| Resource   | Dockerfile         | Script                      | Coolify resource type |
|------------|---------------------|------------------------------|------------------------|
| PlayerSync | `Dockerfile.player` | `futgg_player_sync.py`       | Scheduled Task |
| PriceSync  | `Dockerfile.price`  | `futgg_bulk_price_sync.py`   | Continuous service |
| SBCSync    | `Dockerfile.sbc`    | `easysbc_sbc_sync.py`        | Scheduled Task |

The old per-card Playwright price scraper (`futgg_price_sync.py`) and the
old headed-Chromium FUTBIN SBC scraper (`futbin_sbc_sync.py`) are **not**
deployed by any of these three resources - see each Dockerfile's header
comment for why.

---

## PlayerSync

- **Base directory:** `/auto_sync`
- **Dockerfile:** `/Dockerfile.player`
- **Domain:** none
- **Ports:** none
- **Coolify resource type: Scheduled Task**, not a normal service.
  `futgg_player_sync.py` is genuinely one-shot (`crawl_once()` runs once
  and exits) - deploying it as a normal service lets Coolify's default
  "restart on exit" policy turn a clean exit into a restart-loop, which
  is the exact bug this whole change fixes (was re-scraping the first 5
  listing pages every few seconds). A Scheduled Task runs the container
  once per cron trigger and lets it exit cleanly with no restart policy
  fighting it.
- **Recommended cron:** `15 5 * * *` (05:15 UTC daily) - matches the
  cadence already documented in the script's own module docstring
  ("Recommended Railway Cron: once daily").

**Required env vars:**
- `DATABASE_URL` - required, no default.

**Optional tuning env vars** (all have production-safe defaults - see
`futgg_player_sync.py`'s module docstring for the full list and current
values): `PLAYWRIGHT_HEADLESS`, `PLAYWRIGHT_TIMEOUT_MS`,
`FUTGG_PLAYER_LIMIT`, `FUTGG_LISTING_FULL_SCAN`,
`FUTGG_LISTING_MAX_PAGES`, `FUTGG_LISTING_MAX_PAGES_FULL`,
`FUTGG_LISTING_IDLE_ROUNDS`, `FUTGG_LISTING_PAGE_DELAY`,
`FUTGG_REFRESH_EXISTING`, `FUTGG_PLAYER_REQUEST_DELAY`,
`FUTGG_PLAYER_CONCURRENCY`, `FUTGG_CIRCUIT_BREAKER_THRESHOLD`.

---

## PriceSync

- **Base directory:** `/auto_sync`
- **Dockerfile:** `/Dockerfile.price`
- **Domain:** none
- **Ports:** none
- **Coolify resource type: continuous service** (Coolify's normal
  always-on resource, restart policy "always"/"unless stopped" -
  **not** a Scheduled Task). `futgg_bulk_price_sync.py` is already
  long-running: its `__main__` calls `run_forever()`, which holds one
  Playwright browser/page open for the process lifetime and loops
  internally (ingest cycle -> sleep `FUTGG_BULK_INTERVAL_SECONDS` ->
  repeat). Running it as a Scheduled Task would pay a fresh browser
  cold-start every trigger and fight itself if a trigger overlaps a
  still-running cycle - deploy it once and leave it running.

**Required env vars:**
- `DATABASE_URL` - required, no default.
- `FUTGG_BULK_APPLY` - **set to `true`** to actually write prices.
  Already baked into `Dockerfile.price` as `ENV FUTGG_BULK_APPLY=true`
  (matching the script's own default), so you do not need to set this in
  Coolify unless you want to override it to `false` for a temporary
  read-only coverage check.

**Optional tuning env vars:** `FUTGG_BULK_PLATFORM` (default `ps5`),
`FUTGG_BULK_INTERVAL_SECONDS` (default `300`, minimum `60`),
`PLAYWRIGHT_HEADLESS` (default `true`). See
`futgg_bulk_price_sync.py`'s module docstring for the full mechanism.

---

## SBCSync

- **Base directory:** `/auto_sync`
- **Dockerfile:** `/Dockerfile.sbc`
- **Domain:** none
- **Ports:** none
- **Coolify resource type: Scheduled Task**, not a normal service - same
  reasoning as PlayerSync. `easysbc_sbc_sync.py`'s canonical script
  (`crawl_once()`) is one-shot, and its own module docstring says so
  explicitly: "Same one-shot-per-invocation Cron Job design ... not a
  permanent worker, no Procfile entry."
- **Canonical script: `easysbc_sbc_sync.py`, not `futbin_sbc_sync.py`.**
  `futbin_sbc_sync.py` (Playwright + Xvfb, headed Chromium) is confirmed
  NOT reliably usable - FUTBIN's Cloudflare hard-blocks every network
  origin tested, including a real Railway deployment where Xvfb/Chromium
  worked correctly right up to the actual page request (immediate HTTP
  403 "Just a moment..." - a WAF block on IP/network origin, not a
  solvable JS challenge). `easysbc_sbc_sync.py` gets the same data
  (`market_events`/`sbc_details`/`sbc_challenges`, `source='easysbc'`)
  from EasySBC.io's plain, unauthenticated JSON API instead - aiohttp
  only, no browser, no Cloudflare challenge to fight. See
  `easysbc_sbc_sync.py`'s own module docstring and `README.md`'s "SBC
  collector" sections for the full history.
- **Recommended cron:** `30 6 * * *` (06:30 UTC daily, staggered from
  PlayerSync) - "once daily, not more often" per the same caution
  `README.md` already documents for the FUTBIN-based collector; SBC sets
  don't change often enough to justify more frequent runs, and this API's
  rate-limit behavior at real crawl volume hasn't been proven yet (see
  `easysbc_sbc_sync.py`'s "still genuinely open" section). **Do one
  supervised manual run and check the log/heartbeat output before
  scheduling** - same discipline the script's own docstring asks for.

**Required env vars:**
- `DATABASE_URL` - required, no default.

**Optional tuning env vars:** `EASYSBC_LISTING_PAGE_SIZE` (default `50`),
`EASYSBC_MAX_LISTING_PAGES` (default `50`), `SBC_DETAIL_STALE_HOURS`
(default `20`), `SBC_MAX_DETAIL_PAGES` (default `0` = no cap),
`SBC_REQUEST_DELAY_SECONDS` (default `1.5`), `SBC_MAX_RETRIES`
(default `3`).

---

## If PlayerSync/PriceSync/SBCSync already exist as Coolify resources

If a `futgg_player_worker`-style resource is already deployed as a
**normal service** (the restart-loop bug this document fixes), it needs
to be **deleted and recreated** as a Scheduled Task - Coolify does not
offer an in-place "convert this service to a Scheduled Task" action, and
a Scheduled Task is a different resource type, not a config toggle on an
existing service. Recreate it pointing at `Dockerfile.player` per the
table above, then delete the old always-on resource once the Scheduled
Task has run successfully.

Any existing SBC-worker resource built from the old `Dockerfile.sbc`
(headed Chromium + Xvfb + `futbin_sbc_sync.py`) should also be **deleted
and recreated**: `Dockerfile.sbc` now builds a different, much smaller
image (`python:3.11-slim`, no Playwright/Xvfb) running
`easysbc_sbc_sync.py` instead - a straight redeploy of the same resource
picks this up automatically (rebuild from the updated Dockerfile), but if
that resource was previously set up as a continuous service rather than a
Scheduled Task, it still needs recreating as a Scheduled Task per the
table above.

A resource already correctly running `futgg_bulk_price_sync.py` via
`Dockerfile.price` as a continuous service needs no structural change -
redeploying picks up the new Dockerfile content (explicit
`FUTGG_BULK_APPLY=true`) with no resource-type change required.
