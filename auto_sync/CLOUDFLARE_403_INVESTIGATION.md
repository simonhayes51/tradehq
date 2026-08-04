# SalesSync Cloudflare 403 investigation

**Status: root cause CONFIRMED as headless-browser detection, not
transport - fixed via headed Chromium (Xvfb).** This document's original
hypothesis (below) was that only the injected-`fetch()` transport was
being caught, and a fully organic page navigation - matching how
PriceSync has always worked - would bypass the challenge entirely. A
follow-up production diagnostic run **disproved that specific
hypothesis**: a `page_capture` navigation (the fully organic transport
built to test it) was challenged identically to `signed_fetch` on the
exact same card. That same run's own outbound Google Analytics traffic
carried the real answer: `navigator.userAgentData` (User-Agent Client
Hints) reported `HeadlessChrome`, regardless of the spoofed `User-Agent`
HEADER this worker sets - Playwright's UA override patches the header,
not the Client Hints API, so Cloudflare's bot management can still see
through it. `Dockerfile.sales` now runs headed Chromium against a real
(virtual) X11 display via Xvfb (`docker-entrypoint-sales.sh`,
`PLAYWRIGHT_HEADLESS=false`) - the same headed-vs-headless difference
already proven for FUTBIN's Cloudflare elsewhere in this repo's history,
though FUTBIN ultimately still blocked headed traffic for an unrelated
IP/network-level WAF reason, which is not evidence against this fix
working for FUT.GG's differently-configured Cloudflare. See
`COOLIFY.md`'s "Headed Chromium via Xvfb" section for the full
before/after evidence and the one-card diagnostic command to confirm it
in production.

`futgg_sales_sync.py`'s `page_capture`/`auto` transport machinery (added
in response to the original, now-superseded hypothesis) is left in
place - it remains a legitimate reliability safeguard against
throttling/blocking independent of the headless finding - but it was not
itself the fix for this 403. The rest of this document is kept as the
original evidence trail; read it as history, not as the current
diagnosis.

## The established facts (from the production diagnostic run)

- Anchor page navigation succeeds (`page.goto()` completes, no challenge
  page at the navigation level).
- `POST /api/fut/price-access/sign/` returns HTTP 200.
- The resulting signed `GET <url>?verify=...` returns HTTP 403.
- The 403 response body begins with `<!DOCTYPE html><title>Just a
  moment...</title>` - Cloudflare's own interstitial challenge page, not
  an application-level 403 from FUT.GG's own backend.

This rules out a few things immediately, by definition of what that body
means: it is not a CORS failure (a CORS-blocked response never reaches
JS with a readable body at all - `dataRes.status` wouldn't be readable as
403), and it is not FUT.GG's own API rejecting the request (a JSON API
returning its own 403 would not serve an HTML challenge page instead of
its normal JSON envelope). Cloudflare's edge, sitting in front of
FUT.GG's origin, is intercepting this specific request and choosing to
challenge it instead of proxying it through.

## Side-by-side: what each script actually does

| | **PriceSync** (`futgg_bulk_price_sync.py`) - works | **SalesSync** (`futgg_sales_sync.py`) - 403s |
|---|---|---|
| Browser launch args | `headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"]` | **Identical**, byte-for-byte |
| `new_context()` | `user_agent=USER_AGENT, locale="en-GB"` | **Identical** - same `USER_AGENT` string, same locale |
| `USER_AGENT` value | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36` | **Identical**, character-for-character |
| `page.route()` / `context.route()` interception | Never called | Never called - **identical: none** |
| Number of page navigations per process lifetime | One (`page.goto(url, ...)` inside `harvest_feed`, called again each cycle against the *same resolved URL*, or `FUTGG_DISCOVER_URL` on rediscovery - always a real navigation to the target's own page) | One, ever (`page.goto(anchor, ...)` runs exactly once in `run_forever()` at process startup; the anchor is then **never revisited or renavigated for the rest of the process's life**) |
| How the *target's own data* is obtained | **Never fetched directly.** `page.on("response", on_response)` is attached, then the script navigates to that *same card's own player page* and simply listens for whatever the page's own React/Next.js application requests during ordinary hydration (`r2.fut.gg/26/player-prices-index...json` / `-dyn...json`) - i.e. these are the FUT.GG application's own organic requests, not requests this script constructs | **Actively constructed by injected script.** `page.evaluate(_FETCH_SCRIPT, {...})` runs code Playwright injects into the page (via CDP `Runtime.evaluate`) that itself calls `fetch('/api/fut/price-access/sign/', {...})` and then `fetch(signedUrl, {...})` - these are **not** requests the FUT.GG application's own bundle ever issues on its own; they are synthesized by this script |
| Relationship between the page navigated to and the data requested | **Same target.** The page navigated to *is* the card whose prices are being read - a real user loading that exact page would trigger the exact same requests | **Never the same target.** The page navigated to is one single "anchor" card (whichever currently has the highest rating); every `fetch()` call afterwards requests **a different card's** signed price data - hundreds/thousands of different `eaId`s, over the anchor page's single session, none of which the anchor page ever actually renders |
| Endpoint being hit | `r2.fut.gg` static JSON feed files - **no signing, no per-card token, no `/api/fut/price-access/sign/` involved at all** (confirmed by that module's own docstring: "no signing, no per-card rendering, no rate limit to trip") | `/api/fut/price-access/sign/` (Cloudflare-fronted, per the incident's own evidence) **and** the signed data URL it returns - the *only* worker in this codebase that exercises this signed flow via injected `fetch()` |
| Request volume pattern per session | One page load -> the app's own hydration fires a small, fixed number of requests (2 feed files) once | One page load -> repeated batches of `BATCH_SIZE` (default 8) concurrent signed fetches via `Promise.all`, every `CYCLE_SLEEP`/`BATCH_DELAY` seconds, for the entire process lifetime (hours/days) - potentially thousands of signed requests from one never-renavigated session |

## The first behavioural difference

Reading both scripts top-to-bottom, the first point where they diverge in
*how a protected resource is obtained* is:

> **PriceSync never issues a `fetch()` call of its own. It navigates to
> the exact page whose data it wants and passively listens to whatever
> that page's own application code requests during normal hydration.
> SalesSync navigates to one single, unrelated "anchor" page once, and
> then actively injects and executes its own `fetch()` calls - via
> `page.evaluate()` - against a *different* endpoint (the signed
> price-access flow) for hundreds of *other* cards it never
> navigates to, repeated continuously for the life of the process.**

This is not a guess about *why* Cloudflare treats them differently - it
is the literal, provable structural difference between the two request-
generation mechanisms, established by direct comparison of the two files
(`futgg_bulk_price_sync.py`'s `harvest_feed()` vs
`futgg_sales_sync.py`'s `_FETCH_SCRIPT` + `run_forever()`/
`process_batch()`). Whether *this specific difference* is the reason
Cloudflare serves a challenge page - as opposed to, say, the sheer
sustained volume of signed requests from one long-lived session, or
something about the signed-request headers/cookies themselves - is
exactly what the new instrumentation below is for. That determination
requires reading real production logs; it cannot be settled from source
code alone, and this document does not claim otherwise.

### A secondary, minor difference worth noting

SalesSync's anchor-selection query omits `AND is_tradeable IS DISTINCT
FROM FALSE` (present in PriceSync's `resolve_player_url()`):

```sql
-- PriceSync (futgg_bulk_price_sync.py, resolve_player_url):
SELECT source_url FROM futgg_players
WHERE is_active AND source_url IS NOT NULL
  AND is_tradeable IS DISTINCT FROM FALSE
ORDER BY rating DESC NULLS LAST LIMIT 1

-- SalesSync (futgg_sales_sync.py, run_forever):
SELECT source_url FROM futgg_players
WHERE is_active AND source_url IS NOT NULL
ORDER BY rating DESC NULLS LAST LIMIT 1
```

This could occasionally pick an untradeable card as the anchor page,
which is unlikely to be *the* explanation for a 403 on the signed data
endpoint (the anchor page itself loads successfully per the production
logs), but it's a real, confirmed discrepancy and cheap to align - not
fixed in this pass, since the instructions for this investigation were
diagnostics only, no speculative fixes.

## What was explicitly ruled out by direct comparison

- **Browser fingerprint / launch configuration**: identical between the
  two scripts (same UA string, same locale, same launch args, no
  `viewport` set by either, same headless setting driven by the same
  `PLAYWRIGHT_HEADLESS` env var).
- **Playwright request interception**: neither currently-deployed worker
  calls `page.route()`/`context.route()` anywhere (grep-confirmed across
  every `.py` file in `auto_sync/`). The retired, undeployed
  `futgg_price_sync.py` does use `context.route()` to block images/fonts
  - not relevant to either running worker.
- **CORS as the mechanism**: ruled out by the reported evidence itself -
  a CORS-blocked fetch never exposes a readable `.status` to JS.

## What is still open (needs the new instrumentation's real output)

- Whether Cloudflare cookies (`cf_clearance`, `__cf_bm`) exist in the
  browser context *before* the signed request, and whether they're
  actually attached to it (`hasCookieHeader`/cookie names in the new
  `NETDIAG REQUEST` log lines).
- Whether `cf-ray`/`cf-cache-status`/`server` on the 403 response differ
  in any informative way from the *same headers* on the successful sign
  response, or from PriceSync's own successful `r2.fut.gg` responses (run
  the one-card diagnostic mode and compare byte-for-byte against a
  PriceSync log from the same time window).
- Whether `Sec-Fetch-Site`/`Sec-Fetch-Mode`/`Sec-Fetch-Dest` are present
  and what values they carry on the signed request (the browser sets
  these automatically and Playwright does not let JS override them, but
  their *values* - e.g. `Sec-Fetch-Site: same-origin` vs something else -
  are worth confirming rather than assumed).
- `navigator.webdriver`'s actual runtime value (Playwright's Chromium
  does not hide this by default; PriceSync has never needed it hidden
  because it never issues its own protected-resource `fetch()` calls in
  the first place - if SalesSync's synthesized fetches are what
  Cloudflare's bot-management is keying off, this is one of the more
  commonly-checked signals).

None of these should be assumed from this document - they are exactly
what `FUTGG_SALES_DIAGNOSTIC_CARD_ID`'s new logging exists to show from a
real run.

## Instrumentation added

`futgg_sales_sync.py`'s one-card diagnostic mode
(`FUTGG_SALES_DIAGNOSTIC_CARD_ID=<id>`, ideally combined with
`FUTGG_SALES_APPLY=false`) now also logs, for that one run:

- `capture_context_diagnostics()` - once, before the fetch: configured
  and runtime `navigator.userAgent`, `navigator.webdriver`, whether
  `page.route()`/`context.route()` interception is active (statically
  `False` - confirmed by code, logged for the record), confirmation that
  the fetch executes in the page's own JS context via
  `page.evaluate()`/CDP `Runtime.evaluate` (never Python/aiohttp), every
  cookie *name* present in the browser context, and specifically which of
  those look Cloudflare-shaped (`__cf_bm`, `cf_clearance`, `__cflb`,
  `__cfruid`, `__cfwaitingroom`, or anything else prefixed `cf_`/`__cf`).
- `_attach_network_diagnostics()` - for every request/response the page
  makes for the rest of that diagnostic run: method, URL (secrets
  redacted), resource type, **every header the browser actually
  sent/received** (via `request.all_headers()`/`response.all_headers()`,
  which - unlike the synchronous `.headers` property - include
  browser-computed headers Playwright doesn't expose any other way, e.g.
  `cookie`, `sec-fetch-*`), whether a `Cookie` header was present at all,
  the `Sec-Fetch-*` headers pulled out separately for visibility, and
  Cloudflare-specific response headers (`cf-ray`, `cf-cache-status`,
  `cf-mitigated`, `server`, etc.) pulled out separately too.
- A before/after Cloudflare-cookie diff, logged once the fetch completes.

**Redaction, matching `futgg_discover.py`'s already-proven approach**:
`futgg_instrumentation.redact_headers()` marks `cookie`, `authorization`
and other sensitive header names as present-but-redacted (never their
value); `redact_url()` strips `verify=`/token-shaped query parameters
before a URL is ever logged; `redact_value()` further truncates/masks any
long, token-shaped string. Tests
(`tests/test_futgg_sales_sync_integrity.py`,
`TestIsCfCookie`/`test_capture_context_diagnostics_*`/
`test_network_diagnostics_*`) assert a real cookie/token value planted in
fake request/response fixtures never appears in the logged text.

### Running it

```bash
FUTGG_SALES_DIAGNOSTIC_CARD_ID=<a currently-403ing card's source_card_id> \
FUTGG_SALES_APPLY=false \
DATABASE_URL=postgresql://... python futgg_sales_sync.py
```

Read the `NETDIAG REQUEST`/`NETDIAG RESPONSE` lines for the sign call and
the signed data call, plus the `DIAGNOSTIC: browser/context state: ...`
and `DIAGNOSTIC: cloudflare cookies AFTER request=...` lines, and compare
directly against the "What is still open" questions above.
