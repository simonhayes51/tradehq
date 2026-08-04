# Deploying `auto_sync` on Coolify

Four independent Dockerfile-based Coolify resources, all built from this
same `/auto_sync` base directory, each with its own dedicated Dockerfile.
None of them expose a port or need a domain - they only ever talk
outbound (to fut.gg / api-fc26.easysbc.io) and to `DATABASE_URL`.

All four point `DATABASE_URL` at the **same single Postgres database**
used by `backend` - there is no separate auto_sync database. Do not
create a new Postgres resource for these; use the connection string of
the Postgres instance already backing the rest of the deployment.

| Resource   | Dockerfile         | Script                      | Coolify resource type |
|------------|---------------------|------------------------------|------------------------|
| PlayerSync | `Dockerfile.player` | `futgg_player_sync.py`       | Scheduled Task |
| PriceSync  | `Dockerfile.price`  | `futgg_bulk_price_sync.py`   | Continuous service |
| SBCSync    | `Dockerfile.sbc`    | `easysbc_sbc_sync.py`        | Scheduled Task |
| SalesSync  | `Dockerfile.sales`  | `futgg_sales_sync.py`        | Continuous service |

The old per-card Playwright price scraper (`futgg_price_sync.py`) and the
old headed-Chromium FUTBIN SBC scraper (`futbin_sbc_sync.py`) are **not**
deployed by any of these four resources - see each Dockerfile's header
comment for why.

None of these four services take a command override in Coolify - each
Dockerfile bakes in the correct default `CMD` for its script, so
deploying is just "pick the right Dockerfile" (see each resource's
**Dockerfile** field below).

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

### Self-healing rediscovery

PriceSync picks one player page at startup (`resolve_player_url()`,
highest-rated active/tradeable card in our own catalogue) and reuses that
same URL for every cycle. If that specific page stops yielding the bulk
feed - the card became untradeable/inactive without the flag updating,
FUT.GG restructured something, or the page otherwise stops triggering the
feed fetch - every cycle used to fail identically
(`Feed not captured (index=False dyn=False)`) until someone noticed and
manually ran `futgg_discover.py` against a known-good URL, then restarted
the worker. **That manual step should no longer normally be needed** -
the worker now retries automatically against `FUTGG_DISCOVER_URL` on the
same warm browser/page, and adopts it as the new `PLAYER_URL` for every
future cycle if it works, continuing through decode/coverage/write in the
same cycle rather than waiting for the next scheduled interval.

- `FUTGG_DISCOVER_URL` - **strongly recommended.** A stable,
  operator-maintained "known good" FUT.GG player URL to fall back to.
  Not set by default (never hardcoded into the image or the script) - set
  it in Coolify. Current confirmed-working example:
  `https://www.fut.gg/players/245037-eric-garcia/26-84131117/` (Eric
  Garcia) - this is just today's example value, not a permanent constant;
  if it ever stops working, replace it with any other current, real
  FUT.GG player URL and redeploy/restart. Without this set, automatic
  recovery cannot do anything - the worker logs that clearly every time
  capture fails, rather than silently skipping it.
- `FUTGG_DISCOVERY_ENABLED` - default `true`. Set to `false` to restore
  the old behaviour (fail every cycle until someone notices and
  intervenes by hand).
- `FUTGG_DISCOVERY_RETRY_COUNT` - default `1`. How many rediscovery
  attempts (each one page load against `FUTGG_DISCOVER_URL`) to make
  within a single failed cycle before giving up for that cycle.
- `FUTGG_DISCOVERY_FAILURE_BACKOFF_SECONDS` - default `900` (15 minutes).
  How long to sleep before the next cycle after a feed-capture failure
  that automatic rediscovery could not fix - deliberately longer than
  `FUTGG_BULK_INTERVAL_SECONDS`'s normal 300s cadence, so a broken feed
  doesn't get hammered every 5 minutes while it's down.

**Manual `futgg_discover.py` runs should no longer normally be needed**
for this specific failure mode - only reach for it if automatic
rediscovery itself starts failing against `FUTGG_DISCOVER_URL` too (i.e.
the fallback URL itself has gone stale), to find a new one, or for the
unrelated diagnostic job it was originally built for (classifying *how*
FUT.GG's price data is served - see its own module docstring).

---

## SalesSync

- **Repository:** `simonhayes51/tradehq`
- **Branch:** `main`
- **Resource type:** Continuous Application
- **Build Pack:** Dockerfile
- **Base Directory:** `/auto_sync`
- **Dockerfile:** `/Dockerfile.sales`
- **Ports:** None
- **Domain:** None
- **Command:** no command override needed - `Dockerfile.sales`'s
  `CMD ["python", "futgg_sales_sync.py"]` is the resource's default
  command already, run directly with no shell wrapper. Selecting
  `Dockerfile.sales` as the build Dockerfile is the entire deployment
  step.
- **Coolify resource type: continuous service** (Coolify's normal
  always-on resource, restart policy "always"/"unless stopped" - **not**
  a Scheduled Task). `futgg_sales_sync.py` is already long-running: its
  `__main__` calls `run_forever()`, which loads one fut.gg anchor page
  once and holds it open for the process lifetime, looping internally
  (select due cards -> batch sign+fetch -> sleep `CYCLE_SLEEP`/
  `IDLE_SLEEP` -> repeat). This is the same continuous-service shape as
  PriceSync, not the one-shot shape PlayerSync/SBCSync use - do not
  redeploy it as a Scheduled Task, and do not add an outer loop wrapper
  around it (it already has one).

**Required env vars:**
- `DATABASE_URL` - required, no default. The same shared database
  `backend`, PriceSync and SBCSync use - no separate SalesSync database.

**Optional tuning env vars** (all have production-safe defaults - see
`futgg_sales_sync.py`'s own top-of-file constants for current values):
`FUTGG_GAME_YEAR` (default `26`), `FUTGG_SALES_PLATFORM` (default `ps5`),
`PLAYWRIGHT_HEADLESS` (default `true`), `PLAYWRIGHT_TIMEOUT_MS` (default
`45000`), `FUTGG_SALES_BATCH_SIZE` (default `8`), `FUTGG_SALES_BATCH_DELAY`
(default `0.5`), `FUTGG_SALES_CYCLE_SLEEP` (default `5.0`),
`FUTGG_SALES_IDLE_SLEEP` (default `30.0`), `FUTGG_SALES_SELECT_LIMIT`
(default `200`), `FUTGG_SALES_85_PLUS_INTERVAL_MIN` (default `60`),
`FUTGG_SALES_80_84_INTERVAL_MIN` (default `240`),
`FUTGG_SALES_75_79_INTERVAL_MIN` (default `720`),
`FUTGG_SALES_UNDER_75_INTERVAL_MIN` (default `2880`).

### Data-integrity + resilience env vars (added for the 2026-08-04 incident)

- `FUTGG_SALES_APPLY` - default `true`. Set to `false` for a dry run
  (nothing written to the database, would-be writes only logged) -
  combine with `FUTGG_SALES_DIAGNOSTIC_CARD_ID` below for a fully
  read-only single-card test.
- `FUTGG_SALES_FAILURE_RETRY_BACKOFF_MINUTES` - default `2`. How long a
  *previously-read* card whose refresh attempt failed waits before being
  retried - always far shorter than any real `sales_interval_minutes()`
  tier (minimum 5), so it's never confused with genuine coverage. A
  *never-read* card whose attempt fails is never given this backoff at
  all - it stays exactly `NULL` (still due immediately, still counted in
  `never_read`) rather than getting any schedule write on failure.
- `FUTGG_SALES_CIRCUIT_BREAKER_THRESHOLD` - default `0.8` (80%). Once a
  cycle's running failure ratio reaches this (see
  `FUTGG_SALES_CIRCUIT_BREAKER_MIN_SAMPLE` below), the cycle stops
  selecting/marking further cards immediately instead of grinding through
  the rest of `FUTGG_SALES_SELECT_LIMIT`.
- `FUTGG_SALES_CIRCUIT_BREAKER_MIN_SAMPLE` - default `20`. Minimum cards
  attempted before the breaker can trip - a single unlucky 8-card batch
  must not open it.
- `FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS` - default `300` (5
  minutes). Sleep before the next cycle when the breaker trips, instead
  of the normal `FUTGG_SALES_CYCLE_SLEEP` (5s) - this is what stops the
  "200-cards-failed every few seconds" pattern from the incident logs.
- `FUTGG_SALES_DIAGNOSTIC_CARD_ID` - unset by default. When set to a real
  `source_card_id`, the worker runs a single-card diagnostic pass instead
  of the normal queue loop and exits - see "One-card diagnostic mode"
  below.

### One-card diagnostic mode (item 9)

Test a single known card inside Coolify without touching the whole queue
- e.g. temporarily override this Coolify resource's env/command for one
manual run, or run locally against the same `DATABASE_URL`:

```bash
FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 FUTGG_SALES_APPLY=false \
DATABASE_URL=postgresql://... python futgg_sales_sync.py
```

`84131117` above is Eric Garcia's `source_card_id` (same reference card
PriceSync's `FUTGG_DISCOVER_URL` uses) - substitute any real card id you
want to test, ideally one currently failing in production logs.

**Expected output on a successful fetch** (timestamps/exact values will
differ):
```
... WARNING ========================================================================
... WARNING FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 - single-card diagnostic mode, apply=False
... WARNING ========================================================================
... INFO DIAGNOSTIC: card=84131117 name='Eric Garcia' rating=84 next_sales_due_at=None (never_read=True)
... INFO DIAGNOSTIC: anchor page loaded: https://www.fut.gg/players/...
... INFO DIAGNOSTIC: card=84131117 SUCCESS: 12 completed auction(s) currentPrice=51000 signStatus=200 dataStatus=200 dataContentType='application/json; charset=utf-8' expiresIn=120 signedUrlAbsolute=False signedUrlOrigin='https://www.fut.gg' sameOriginAsPage=True hasVerifyParam=True
... INFO DIAGNOSTIC: would-apply summary: ok=1 failed=0 sales_seen=12 (FUTGG_SALES_APPLY=False - NOT written - dry run)
... WARNING ========================================================================
... WARNING DIAGNOSTIC MODE complete for card=84131117: ok=True
... WARNING ========================================================================
```
Exit code `0`. **On the currently-reported failure** the same run instead
logs a `sales fetch failed card=... error=data_403 signStatus=200
dataStatus=403 dataContentType=... signedUrlAbsolute=... signedUrlOrigin=...
sameOriginAsPage=... hasVerifyParam=...` line (see "Diagnosing the 403"
below for what to read off it) and exits `1`.

### Diagnosing the 403 (root cause - needs confirming from real logs)

This sandbox has no live network access to fut.gg, so the exact cause
could not be directly confirmed here - the diagnostic fields above exist
specifically so the NEXT real failure in Coolify's logs (or a one-card
diagnostic run) answers it directly instead of guessing. What the code
review here established and ruled out, and the leading hypothesis:

- **Not a CORS/browser-level block.** `dataRes.status` is being read as a
  real `403` (not a thrown/opaque response), which only happens for a
  response the browser actually let through to script - i.e. the request
  reached FUT.GG's server/edge and it deliberately rejected it. A CORS
  failure would show up as a caught exception (`error: 'exception:...'`),
  not `data_403`.
- **Signing itself works** (per the observed logs, `sign_502` was rare -
  1 in 200 - while `data_403` was the overwhelming majority), so this is
  specific to the SECOND request, not a broken sign step.
- **Leading hypothesis, matching this repo's own established pattern**
  (FUTBIN's Cloudflare blocking Railway's/a datacentre's network origin
  while a residential connection worked fine - see `README.md`'s SBC
  collector history): FUT.GG's data-serving edge may be blocking this
  Hetzner-hosted Coolify deployment's IP/network origin specifically on
  the signed data endpoint, while the sign endpoint itself (possibly
  served by a less-protected path) still succeeds. This is a hypothesis,
  not a confirmed fact - the new `signedUrlOrigin`/`sameOriginAsPage`
  fields will show directly whether the signed URL even points at the
  same origin as the anchor page (a cross-origin CDN target with a
  stricter WAF would also explain it), and `dataContentType` on a failure
  will show whether the 403 body is JSON (an application-level rejection)
  or `text/html` (a WAF/challenge page) - read that off the next real
  failure or a one-card diagnostic run before assuming which it is.
- Other angles the diagnostic fields also rule in/out directly: token
  expiry (`expiresIn`, compared against how long the sign->fetch gap
  actually was), and a possible new requirement that the requested eaId
  match the anchor page's own card (test with
  `FUTGG_SALES_DIAGNOSTIC_CARD_ID` set to the SAME card whose
  `source_url` is being used as the anchor - if that succeeds while other
  cards still 403, this is confirmed).

### SalesSync incident repair (2026-08-04 18:56 UTC data_403 incident)

The pre-fix worker wrote `next_sales_due_at = NOW() + 5 minutes` on every
failed fetch, including `data_403`/`sign_502` - silently converting
never-read cards into "read (but failed)" and pushing already-read cards'
due dates out with no genuine sales data. See
`auto_sync/sql/repair_salessync_20260804_403_incident.sql` for the exact
repair SQL (preview SELECT, the UPDATE, and a verification SELECT), with
its own header explaining the scoping caveat in detail. **Not run
automatically by anything** - read the file, run the preview SELECT
first, then the UPDATE, against the same database this worker uses.
Never resets a card whose last recorded attempt was `last_sales_status =
'success'`.

---

## SBCSync

- **Repository:** `simonhayes51/tradehq`
- **Branch:** `main`
- **Base directory:** `/auto_sync`
- **Build pack:** Dockerfile
- **Dockerfile:** `/Dockerfile.sbc`
- **Domain:** none
- **Ports:** none - do not expose any port
- **Command:** `python easysbc_sbc_sync.py` (this is `Dockerfile.sbc`'s
  `CMD` already - no override needed, only relevant if you're setting up
  the resource by hand without the Dockerfile build pack)
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
  `easysbc_sbc_sync.py`'s "still genuinely open" section).

**Required env vars:**
- `DATABASE_URL` - required, no default. The same shared database
  `backend` and PriceSync use - no separate SBC database.

**No API key/authentication required.** api-fc26.easysbc.io is a plain,
unauthenticated public JSON API - there is nothing else credential-shaped
to configure for this worker beyond `DATABASE_URL`.

**Optional tuning env vars:** `EASYSBC_LISTING_PAGE_SIZE` (default `50`),
`EASYSBC_MAX_LISTING_PAGES` (default `50`), `SBC_DETAIL_STALE_HOURS`
(default `20`), `SBC_MAX_DETAIL_PAGES` (default `0` = no cap),
`SBC_REQUEST_DELAY_SECONDS` (default `1.5`), `SBC_MAX_RETRIES` (default
`3`), `EASYSBC_LOCK_KEY` (default `7741042` - only change this if it ever
collides with another advisory lock key added elsewhere in this repo).

### One supervised manual run before scheduling

Same discipline this repo already asks for every scraper/collector before
trusting it unattended (see `README.md`). Two steps:

**1. Diagnostic pass (read-only, no database writes, no advisory lock):**
```bash
DATABASE_URL=postgresql://... EASYSBC_DIAGNOSTIC=true python easysbc_sbc_sync.py
```
Confirms the listing/detail/challenge endpoints still return the shape
this script expects, without touching any table. Reach for this first if
a real run ever reports "zero SBC sets found" - it tells you whether the
API's response shape changed (see "if the endpoint/shape has drifted"
below) rather than guessing.

**2. Real run:**
```bash
DATABASE_URL=postgresql://... python easysbc_sbc_sync.py
```
Or, against a Coolify-deployed resource, trigger the Scheduled Task
manually once from its own "Run now" / manual-trigger action instead of
waiting for the cron.

**Expected successful log output** (INFO-level, timestamps/exact counts
will differ):
```
... INFO SBC worker process started (easysbc.io API)
... INFO crawl_once() starting
... INFO Connecting to database...
... INFO Database pool ready
... INFO Ensuring tables exist...
... INFO Tables ready
... INFO Loaded 43 real nation names and 28 real league names from fut_players for requirement classification
... INFO Fetching SBC set listing from https://api-fc26.easysbc.io...
... INFO Listing: 37 sets found
... INFO SBC details due: 12 of 37 (25 unchanged/not yet stale, skipped)
... INFO Loading SBC detail 1/12: 1 of 4 95+ FOF or FUTTIES T1 Player Pick (id=1293)
...
... INFO Run complete. elapsed=18.4s sets_found=37 sets_inserted=3 sets_updated=34 sets_due=12 sets_skipped_not_due=25 details_attempted=12 details_written=12 details_failed=0 http_429=0 http_5xx=0 http_non_200=0 http_exceptions=0 json_decode_errors=0 set_parse_failed=0 zero_challenges=0
```
Exit code `0`. A failed run instead ends with a `Run complete. ... ok=False`
-shaped summary (or, for zero sets found, `Zero sets found on listing
endpoint`) and exits non-zero - Coolify's own Scheduled Task run history
shows this as a failed run, not just a log line to notice.

### If the endpoint/shape has drifted

Per the module docstring, several things about EasySBC's API were
confirmed manually but not exhaustively (the listing endpoint's exact
envelope shape, rate-limit behavior at real volume, `categoryId`/reward-id
mappings). If a real run starts reporting `set_parse_failed`,
`listing_shape_unrecognised`, or "zero sets found" where there previously
were sets, **run the diagnostic pass above first** rather than guessing
at a fix - it logs the actual current response shape (top-level keys,
first item's keys, parsed-vs-raw comparison) so any change can be
confirmed and `_extract_list()`/`parse_set_fields()`/
`parse_challenge_fields()` updated against real evidence, the same way
this script itself was originally built against real confirmed responses
rather than assumed ones.

---

## If PlayerSync/PriceSync/SBCSync/SalesSync already exist as Coolify resources

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

If an existing `futgg_sales_worker`-style resource is currently deployed
from the **generic** `Dockerfile` with a command override
(`python futgg_sales_sync.py` set by hand in Coolify rather than baked
into the image), switch its **Dockerfile** field to `/Dockerfile.sales`
and **remove the command override** - `Dockerfile.sales` already bakes in
the correct default `CMD`, so Coolify no longer needs to be told what to
run. It stays a continuous service; no resource-type change is required,
only the Dockerfile field and dropping the manual command.
