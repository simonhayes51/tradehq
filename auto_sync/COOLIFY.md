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

### One-off FC27 card database pull

Before FC27 prices and sales are live, use PlayerSync only. This pulls
card/player metadata from FUT.GG's current FC27 catalogue pages and does
not need PriceSync or SalesSync to be running:

```sh
FUTGG_GAME_YEAR=27 \
FUTGG_LISTING_FULL_SCAN=true \
FUTGG_REFRESH_EXISTING=true \
FUTGG_BACKFILL_RELEASE_METADATA=false \
python futgg_player_sync.py
```

`/players/new/` is only a launch/recent-card window, so the full pull must
keep `FUTGG_LISTING_FULL_SCAN=true`; that walks both `/players/new/` and
the main `/players/` catalogue.

Do not leave `FUTGG_BACKFILL_RELEASE_METADATA=true` or
`FUTGG_BACKFILL_GAME_YEAR=26` on this run. Backfill mode reads existing
database rows that are missing release metadata; it does not crawl the
FC27 card catalogue.

### One-off GG Rating backfill

After backend migration `068_futgg_gg_rating.sql` has run, use the
PlayerSync image for a supervised listing-only backfill. It reads the
main `/players/` catalogue and never opens card detail pages. Run one
page first:

```sh
python backfill_futgg_gg_rating.py --game-year 27 --start-page 1 --max-pages 1
```

Check `futgg_players.gg_rating` and the logged found/matched/updated
counts, then resume from the durable database checkpoint:

```sh
python backfill_futgg_gg_rating.py --game-year 27 --max-pages 400
```

`FUTGG_GAME_YEAR` is the production cycle setting and now defaults to
`27`. `--game-year` is the explicit historical override and does not
change production cycle state. `FUTGG_LISTING_PAGE_DELAY`, stabilization,
timeout, headless, and idle-round settings are shared with PlayerSync.
The advisory lock prevents this job and the normal PlayerSync task from
crawling concurrently.

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
- `FUTGG_GAME_YEAR` - **set to `27`** for the current FC27 cycle.
  Already baked into `Dockerfile.price` as `ENV FUTGG_GAME_YEAR=27`;
  override only for an intentional historical backfill.
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
  it in Coolify. Use a current FC27 FUT.GG player URL for the live cycle
  (or a matching historical URL only when `FUTGG_GAME_YEAR` is deliberately
  set to that older cycle). Without this set, automatic recovery cannot
  do anything - the worker logs that clearly every time capture fails,
  rather than silently skipping it.
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
  `CMD ["/app/docker-entrypoint-sales.sh"]` is the resource's default
  command already. Selecting `Dockerfile.sales` as the build Dockerfile
  is the entire deployment step.
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

### Headed Chromium via Xvfb (2026-08-04 headless-detection finding)

Production diagnostics on a real deployed instance confirmed the actual
root cause of the Cloudflare 403: it is **not** about which transport
requests `/api/fut/player-prices/27/<id>/?verify=...` (the `page_capture`
fallback above was built on that premise and it turned out to be wrong) -
a `page_capture` diagnostic run got challenged identically to
`signed_fetch`, on a fully organic page navigation. What differed was
that the browser's own `navigator.userAgentData` (User-Agent Client
Hints) leaked `HeadlessChrome` in that worker's own outbound Google
Analytics traffic (`uafvl=HeadlessChrome;...`), regardless of the spoofed
`User-Agent` HEADER this worker sets - Playwright's UA override patches
the header, not the Client Hints API, so Cloudflare can still see
through it. This matches the same headed-vs-headless difference already
confirmed for FUTBIN's Cloudflare in this repo's history (see
`Dockerfile.sbc`'s git log) - though FUTBIN ultimately still blocked
headed traffic for an unrelated reason (a network/IP-level WAF block),
which is not evidence this won't work for FUT.GG's differently-configured
Cloudflare.

`Dockerfile.sales` now runs headed Chromium against a real (virtual) X11
display: `docker-entrypoint-sales.sh` starts Xvfb on `DISPLAY=:99` with a
bounded (~2s) readiness check before exec'ing `futgg_sales_sync.py`
directly (same idiom as the retired Xvfb-based `Dockerfile.sbc` -
`exec`, not a second process, so SIGTERM/SIGINT still reach the worker
directly for its own graceful shutdown). `PLAYWRIGHT_HEADLESS=false` is
the image's default now - **do not override it back to `true`** on this
Coolify resource; that reintroduces the exact signal being challenged.
No new dependency: Xvfb already ships in the Playwright base image, same
as it did for the old FUTBIN worker.

**Verify with the one-card diagnostic** (below) after any redeploy - the
`(cloudflare_challenged=...)` field in its output is the direct
confirmation headed mode actually cleared the challenge for this site,
not an assumption carried over from FUTBIN's case.

**Required env vars:**
- `DATABASE_URL` - required, no default. The same shared database
  `backend`, PriceSync and SBCSync use - no separate SalesSync database.

**Optional tuning env vars** (all have production-safe defaults - see
`futgg_sales_sync.py`'s own top-of-file constants for current values):
`FUTGG_GAME_YEAR` (default `27`), `FUTGG_SALES_PLATFORM` (default `ps5`),
`PLAYWRIGHT_HEADLESS` (default `false` - see "Headed Chromium via Xvfb"
above; do not set this to `true`), `PLAYWRIGHT_TIMEOUT_MS` (default
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

### Transport env vars (added 2026-08-04, page_capture fallback)

Production evidence confirmed Cloudflare serves a JS challenge
(`cf-mitigated: challenge`) specifically to the `signed_fetch` transport's
injected `fetch()` calls, while the same data loads fine when a real
player page requests it during its own hydration - see
[`CLOUDFLARE_403_INVESTIGATION.md`](./CLOUDFLARE_403_INVESTIGATION.md).
The worker now supports a second, slower-but-unblocked transport
(`page_capture`) and an automatic fallback mode between the two.

- **`FUTGG_SALES_TRANSPORT` - recommended production setting: `auto`
  (also the default if unset).** `signed_fetch` pins the original
  injected-fetch transport; `page_capture` pins the new passive-capture
  transport; `auto` starts on `signed_fetch` and switches the whole
  process to `page_capture` once the circuit breaker sees a run of
  challenge/403-shaped failures, logging the switch
  (`TRANSPORT SWITCH: signed_fetch -> page_capture (...)`). It does not
  retry the blocked transport every cycle - only after
  `FUTGG_SALES_SIGNED_REPROBE_MINUTES` does it try a small `signed_fetch`
  sample again, switching back only if that sample succeeds.
- `FUTGG_SALES_PAGE_CONCURRENCY` - default `2`, **recommended initial
  value `2`**. Number of Playwright pages `page_capture` runs
  concurrently, each reused across many cards (one navigation per card,
  no injected `fetch()`). Keep this conservative - `page_capture` is a
  reliability fallback, not a throughput match for `signed_fetch`; see
  "page_capture throughput" below before raising it.
- `FUTGG_SALES_PAGE_TIMEOUT_MS` - default `15000`. Per-card `page.goto()`
  timeout (`wait_until="domcontentloaded"`, not full page load). A card
  that doesn't respond within this window is treated as failed and never
  advanced, same as any other failure.
- `FUTGG_SALES_PAGE_SETTLE_MS` - default `4000`. How long to wait after
  `domcontentloaded` for the page's own hydration to fire its
  player-prices request, before giving up on that card. This is the
  dominant cost per card - lowering it risks missing a slow-hydrating
  page's response; raising it slows every card down for a rarely-needed
  margin.
- `FUTGG_SALES_SIGNED_REPROBE_MINUTES` - default `60`. In `auto` mode,
  how long to stay parked on `page_capture` before trying a small
  `signed_fetch` sample again. Deliberately long, not immediate - a
  Cloudflare challenge is not something that clears itself in seconds,
  and reprobing too eagerly just burns another sample of failures.

### One-card diagnostic mode (item 9)

Test a single known card inside Coolify without touching the whole queue
- e.g. temporarily override this Coolify resource's env/command for one
manual run, or run locally against the same `DATABASE_URL`:

```bash
# signed_fetch (original transport)
FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 FUTGG_SALES_APPLY=false \
FUTGG_SALES_TRANSPORT=signed_fetch \
DATABASE_URL=postgresql://... python futgg_sales_sync.py

# page_capture (Cloudflare fallback - the exact command to confirm this
# transport works against a currently-403ing card)
FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 FUTGG_SALES_APPLY=false \
FUTGG_SALES_TRANSPORT=page_capture \
DATABASE_URL=postgresql://... python futgg_sales_sync.py
```

`84131117` above is Eric Garcia's `source_card_id` (same reference card
PriceSync's `FUTGG_DISCOVER_URL` uses) - substitute any real card id you
want to test, ideally one currently failing in production logs.

**Expected output on a successful `signed_fetch` fetch** (timestamps/exact
values will differ):
```
... WARNING ========================================================================
... WARNING FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 - single-card diagnostic mode, transport=signed_fetch apply=False
... WARNING ========================================================================
... INFO DIAGNOSTIC: card=84131117 name='Example Player' rating=84 next_sales_due_at=None (never_read=True) source_url=https://www.fut.gg/players/...
... INFO DIAGNOSTIC: anchor page loaded: https://www.fut.gg/players/...
... INFO DIAGNOSTIC: card=84131117 SUCCESS: 12 completed auction(s) currentPrice=51000 signStatus=200 dataStatus=200 dataContentType='application/json; charset=utf-8' expiresIn=120 signedUrlAbsolute=False signedUrlOrigin='https://www.fut.gg' sameOriginAsPage=True hasVerifyParam=True
... INFO DIAGNOSTIC: would-apply summary: ok=1 failed=0 sales_seen=12 (FUTGG_SALES_APPLY=False - NOT written - dry run)
... WARNING ========================================================================
... WARNING DIAGNOSTIC MODE complete for card=84131117: ok=True transport=signed_fetch
... WARNING ========================================================================
```
**Expected output on a successful `page_capture` fetch** (this is the
transport confirmed to work against the same card `signed_fetch` gets
challenged on):
```
... WARNING ========================================================================
... WARNING FUTGG_SALES_DIAGNOSTIC_CARD_ID=84131117 - single-card diagnostic mode, transport=page_capture apply=False
... WARNING ========================================================================
... INFO DIAGNOSTIC: card=84131117 name='Example Player' rating=84 next_sales_due_at=None (never_read=True) source_url=https://www.fut.gg/players/...
... INFO DIAGNOSTIC: navigating to card's own source_url: https://www.fut.gg/players/...
... INFO DIAGNOSTIC: card=84131117 SUCCESS [page_capture]: 12 completed auction(s) currentPrice=51000 dataStatus=200 dataContentType='application/json; charset=utf-8' elapsed=2.84s (cloudflare_challenged=False)
... INFO DIAGNOSTIC: would-apply summary: ok=1 failed=0 sales_seen=12 (FUTGG_SALES_APPLY=False - NOT written - dry run)
... WARNING ========================================================================
... WARNING DIAGNOSTIC MODE complete for card=84131117: ok=True transport=page_capture
... WARNING ========================================================================
```
Exit code `0` on either transport. **On a Cloudflare-blocked run** (e.g.
`signed_fetch` against a card Cloudflare is currently challenging) the
same run instead logs a `sales fetch failed [signed_fetch] card=...
error=data_403 ...` (or, for `page_capture`, `sales fetch failed
[page_capture] card=... error=page_capture_challenge navStatus=200
navCfMitigated=None dataStatus=403 cfMitigated=challenge ...`) line and a
`DIAGNOSTIC: card=... FAILED [...]: ... (cloudflare_challenged=True)`
summary, and exits `1`.

**Reading `navStatus`/`navCfMitigated` vs `dataStatus`/`cfMitigated`**
(added 2026-08-04, after headed Chromium alone did not clear the
production 403 - see `CLOUDFLARE_403_INVESTIGATION.md`): `page_capture`
now reports the player PAGE's own navigation response separately from
the API subrequest's response, because they can fail independently and
the distinction changes the diagnosis:

- `navCfMitigated=challenge` (regardless of `dataStatus`) - Cloudflare
  challenged the page navigation itself (`error=page_capture_nav_challenge`).
  The real FUT.GG app never loaded, so it never had a chance to make its
  own organic price request - this points at something upstream of both
  transports (e.g. the server's IP/network origin), not at
  headless-vs-headed or which transport is used.
- `navStatus=200`, `navCfMitigated=None`, `cfMitigated=challenge` - the
  page loaded fine but the API subrequest specifically was challenged
  (`error=page_capture_challenge`).
- `navStatus=200`, `navCfMitigated=None`, `dataStatus=None` - the page
  loaded fine and was NOT challenged, but no price request was ever
  captured within `FUTGG_SALES_PAGE_SETTLE_MS`
  (`error=page_capture_timeout`) - a different problem (slow hydration,
  JS error) than a Cloudflare block.

### page_capture throughput (estimated, not live-measured)

This sandbox has no network access to fut.gg, so these are **estimates**
derived from the transport's own timeout/settle-time env vars, not a
measured benchmark - confirm with the one-card diagnostic command above
and a short real run before relying on them. Per card, `page_capture`
pays one real navigation (`domcontentloaded`, typically ~1-2s for a
fut.gg player page) plus the fixed `FUTGG_SALES_PAGE_SETTLE_MS` hydration
wait (default 4s) - roughly **5-6s per card per concurrent page worker**:

| `FUTGG_SALES_PAGE_CONCURRENCY` | ~cards/min | Full backfill (10,064 cards) | 85+ band maintenance (3,272 cards) |
|---|---|---|---|
| 1 | ~10-12 | ~14-17 hours | ~4.5-5.5 hours per full pass |
| 2 (recommended) | ~20-24 | ~7-8.5 hours | ~2.3-2.7 hours per full pass |
| 3 | ~30-36 | ~4.5-5.5 hours | ~1.5-1.8 hours per full pass |

For comparison, `signed_fetch`'s target sustained rate is ~67-109
cards/min (see `AdaptiveThrottle`'s own docstring in
`futgg_sales_sync.py`) - `page_capture` is deliberately **not** a
throughput match for it, only a reliability fallback for whenever
Cloudflare blocks the faster transport. In `auto` mode this means: while
parked on `page_capture`, the 85+ band's 60-minute cadence will not be
hit exactly at concurrency 1-2 (a full pass takes longer than the
interval), but backfill and coverage still make steady forward progress,
and every card it reads counts as a genuine read - no synthetic
advancement, same data-integrity guarantees as `signed_fetch`. Raising
`FUTGG_SALES_PAGE_CONCURRENCY` above 3 is not recommended without first
confirming in real logs that Cloudflare isn't reacting to the increased
concurrent-page volume the same way it reacted to `signed_fetch`'s
request volume.

### Diagnosing the 403 (root cause confirmed: headless detection, not transport)

Confirmed against real production diagnostics: the 403 body is
Cloudflare's own `Just a moment...` challenge page, not an application
response (`cf-mitigated: challenge` on the data response). The
transport-switch fix above (`page_capture`/`auto`) was built on an
initial hypothesis - that only `signed_fetch`'s injected `fetch()` calls
were being caught, and a fully organic page navigation would pass
through untouched - which a later diagnostic run **disproved**: a
`page_capture` run against the same card was challenged identically. The
actual differentiator, confirmed from that same run's own outbound
analytics traffic, is the browser running **headless** - see "Headed
Chromium via Xvfb" above for the full evidence and the fix that's
actually driving this (`PLAYWRIGHT_HEADLESS=false` + Xvfb). The
`page_capture`/`auto` transport machinery is left in place regardless -
it is still a legitimate reliability safeguard against transport-level
throttling/blocking independent of the headless finding, just not the
thing that turned out to fix this specific 403. See
[`CLOUDFLARE_403_INVESTIGATION.md`](./CLOUDFLARE_403_INVESTIGATION.md)
for the full evidence-based, side-by-side comparison against PriceSync
(the one currently-working script that also talks to FUT.GG) and exactly
what to read off the network-level diagnostics
(`FUTGG_SALES_DIAGNOSTIC_CARD_ID`'s `NETDIAG REQUEST`/`NETDIAG RESPONSE`
log lines - full headers, Cloudflare response headers, cookies present
before/after, `navigator.webdriver`, whether interception is active) to
confirm or rule out the identified first structural difference. Summary
of what code review alone established and ruled out:

- **Confirmed Cloudflare challenge, not an application 403** (production
  diagnostics: the 403 body is `<!DOCTYPE html><title>Just a
  moment...</title>`, Cloudflare's own interstitial). Not a CORS/browser-
  level block either - `dataRes.status` reads as a real `403`, which only
  happens for a response the browser let through to script; a CORS
  failure would show up as `error: 'exception:...'`, not `data_403`.
- **Signing itself works** (per the observed logs, `sign_502` was rare -
  1 in 200 - while `data_403` was the overwhelming majority), so this is
  specific to the SECOND request, not a broken sign step.
- **The first confirmed structural difference from PriceSync** (full
  side-by-side in `CLOUDFLARE_403_INVESTIGATION.md`): PriceSync never
  issues a `fetch()` of its own - it navigates to the exact page whose
  data it wants and passively listens to that page's own organic
  requests. SalesSync navigates to one unrelated anchor page ONCE and
  then actively injects its own `fetch()` calls (via `page.evaluate()`)
  against a different endpoint (the signed price-access flow), for
  hundreds of *other* cards it never navigates to, repeated continuously
  for the life of the process. Whether this specific difference is *why*
  Cloudflare challenges it - versus, say, sustained request volume from
  one long-lived session, or something about the signed request's
  cookies/headers themselves - is what the network-level diagnostics
  below are for; it is not assumed here.
- Other angles the diagnostic fields also rule in/out directly: token
  expiry (`expiresIn`, compared against how long the sign->fetch gap
  actually was), whether the signed URL is cross-origin
  (`signedUrlOrigin`/`sameOriginAsPage`), and a possible requirement that
  the requested eaId match the anchor page's own card (test with
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
