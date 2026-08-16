# EAFC Live Companion (V1)

A **read-only** Chrome extension that runs inside the EA SPORTS FC 26 Web
App and shows whether the live Transfer Market confirms an existing
EAFC Live trading recommendation for the exact card you're viewing.

It never bids, buys, lists, relists, snipes, or takes any write action
against EA. It only observes responses the Web App already receives and
displays a small local overlay.

## How it works

There are three separate JavaScript execution contexts involved, because
Chrome does not let them share a JS heap - each box below is a real
process/world boundary, and every arrow is a message, never a shared
object:

```
 EA page's own JS  ───(monkey-patched fetch/XHR)──▶  page-bridge.js
  (MAIN world)                                        (MAIN world)
                                                             │
                                          sanitized auctions only
                                     (CustomEvent on window; see below)
                                                             ▼
                                                    market-observer.js
                                                     (ISOLATED world)
                                                             │
                                                             ▼
                                                       content.js
                                                     (ISOLATED world)
                                                             │
                                            chrome.runtime.sendMessage
                                          (card id only - no session data)
                                                             ▼
                                                     background.js
                                                  (service worker - the
                                                   ONLY place this
                                                   extension calls the
                                                   EAFC Live backend)
```

1. **`src/ea-session.js`** / **`src/page-bridge.js`** run in the page's
   `MAIN` world and wrap `window.fetch` / `XMLHttpRequest` to observe
   (never modify) responses from `/ut/game/fc26`, specifically
   `/transfermarket`. No EA session credential (cookie, `X-UT-SID`,
   `X-UT-PHISHING-TOKEN`, password) is ever read - these files only look
   at URLs and response bodies, never headers.

   **Sanitization happens here, in the MAIN world, before anything
   leaves it.** `page-bridge.js` runs `card-detector.js`'s extraction
   over the raw response and dispatches a `CustomEvent` carrying only:

   ```js
   { sourceCardId, tradeId, rating, buyNowPrice, currentBid, startingBid, expires, tradeState }
   ```

   per auction - never the raw `itemData`, never `maskedDefId`, never
   any other field EA's response happens to carry. The isolated world
   never sees the original response body at all.

2. **`src/card-detector.js`** (MAIN world only) turns raw EA auction
   entries into exact card identity: `resourceId` (falling back to
   `definitionId`), **never** `maskedDefId` alone, since that identifies
   the underlying player and collides across special versions.
   **`src/card-grouping.js`** (ISOLATED world only) is its counterpart -
   just `groupAuctionsByCardId()`, resolving identity over an
   already-sanitized auction list (it has no raw `itemData` to work
   with, by design). These are two separate files with some duplicated
   logic, not one file loaded in both worlds - see the next paragraph
   for why.

   **Why not just load one `card-detector.js` in both worlds?** That was
   the original design, and it's the obvious thing to try. Confirmed
   live against the real EA FC 26 Web App: a script file referenced from
   BOTH of manifest.json's `content_scripts` entries only ever executes
   once for a given page - Chrome silently skips the second injection,
   even though the two entries declare different `"world"` values. There
   was no error, no warning, nothing - the MAIN-world copy ran and
   attached correctly (confirmed via direct logging), the ISOLATED-world
   copy of that exact same file simply never ran at all, while every
   *other* file unique to the isolated world's own entry executed fine.
   `debug-flag.js` had the identical problem for the same reason (also
   referenced from both entries) - `src/debug-flag-isolated.js` is its
   split-out counterpart, one line, kept in sync manually. **Lesson: a
   file path must never appear in more than one `content_scripts` entry
   in this extension**, regardless of `world`. If a future change needs
   the same logic in both worlds again, duplicate the file (or the
   specific function) rather than sharing the path.

3. **`src/market-observer.js`** listens for the sanitized event, groups
   it by card identity, and notifies `content.js`.

4. **`src/eafc-api.js`** (isolated world) is a thin `chrome.runtime`
   messaging shim - it does **not** call the backend directly. It asks
   **`src/background.js`** (the MV3 service worker) to do that.

   **Why the fetch lives in the background worker, not the content
   script:** a content script's `fetch()` executes with the *page's*
   origin for CORS purposes, not the extension's - `host_permissions`
   do not reliably bypass CORS for it the way they do for an extension
   page (popup/options/**background**). The backend's CORS allowlist
   (`backend/main.py`'s `ALLOWED_ORIGINS`) only accepts
   `app.futhub.co.uk` / `app.eafc.live` / localhost - it was **not**
   loosened to accept the EA page's origin or a `chrome-extension://`
   origin, because that would weaken the backend's origin allowlist for
   *every* caller, not just this extension. Doing the fetch from the
   background service worker sidesteps the question entirely: with
   `https://api.eafc.live/*` in `host_permissions`, an extension
   page's fetch is exempt from CORS outright, independent of the
   backend's `Access-Control-Allow-Origin` response.

5. **`src/background.js`** performs the actual `GET
   /api/v2/companion/card/{source_card_id}` call - a plain
   unauthenticated GET carrying only the numeric card id, no headers, no
   body, no EA session value (it has no access to the EA page's DOM at
   all - service workers don't have one). It returns a structured
   result, never a bare intelligence object, so a transient failure
   can't be confused with a definitive answer:

   ```js
   { status: "success",       data: <companion intelligence object> }
   { status: "not_found",     data: null }  // backend 404
   { status: "server_error",  data: null }  // backend 5xx / unexpected status
   { status: "network_error", data: null }  // unreachable / messaging failed
   { status: "timeout",       data: null }  // exceeded REQUEST_TIMEOUT_MS
   ```

   Caching/retry policy is delegated to **`src/intelligence-cache.js`**
   (pure, unit-tested): `success` is cached for
   `SUCCESS_CACHE_TTL_MS` (5 min default), `not_found` for a much
   shorter `NOT_FOUND_CACHE_TTL_MS` (60s default), and
   `network_error`/`server_error`/`timeout` are **never cached** - the
   next detection of the same card retries immediately rather than being
   locked out for the rest of a TTL window over a transient outage.

6. **`src/market-calc.js`** (pure logic) combines the backend
   intelligence with the live EA auctions: cheapest BIN, listing counts
   under target/fair value, nearest expiry, after-tax profit at the
   cheapest listing, and `verdictLabel()` - the reason-aware
   confirm/no-confirm text (`OPPORTUNITY CONFIRMED`,
   `NO LIVE BIN LISTINGS`, `LIVE MARKET — HOLD (NOT A BUY)`, etc. - see
   that function for the full mapping). It never invents a BUY signal -
   a live market with no backend intelligence, or a backend signal that
   isn't `buy`/`strong_buy`, always resolves to "no confirmation".

7. **`src/overlay.js`** renders a small panel (closed Shadow DOM, its
   own styling) in the bottom-right corner. Closing it (`×`) hides only
   that specific result - a distinct card or a changed set of live
   listings for the same card reopens the panel automatically; it does
   not require a page reload.

8. **`src/content.js`** wires 3, 4 and 7 together, and is the only place
   that decides whether a failed intelligence request is allowed to
   retry: a `network_error`/`server_error`/`timeout` result deliberately
   does **not** update the "last rendered" dedupe key, so the same
   unchanged market state re-triggers a backend call on its next
   detection instead of being permanently suppressed by a single
   transient failure.

## Loading the extension

1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `eafc-live-extension/` folder.
3. Confirm the extension loaded with no errors (click **Errors** on its
   card if Chrome flags any - a missing/misnamed file here is the most
   likely cause).
4. Open the EA FC 26 Web App and search the Transfer Market normally.

**After any `manifest.json` change** (e.g. pulling this fix): reload the
extension from `chrome://extensions` (the ↻ icon on its card) AND
reload/renavigate any already-open EA Web App tab. `matches` patterns
are only evaluated when a page loads - a tab opened before the manifest
changed keeps running whatever content scripts were injected under the
old rules, even after the extension itself reloads.

## Configuration

- **`src/debug-flag.js`** - single source of truth for the `[EAFC Live]`
  console-logging toggle (`globalThis.EAFC_LIVE_DEBUG`), loaded
  independently in all three execution contexts (MAIN world, isolated
  world, background worker) since they don't share a JS heap. Flip to
  `true` to enable logging everywhere at once. Never logs an EA session
  credential, regardless of this flag.
- **`src/config.js`** - `API_BASE_URL`, cache TTLs, request timeout.
  Read by the background service worker (via `importScripts`) and, for
  reference, the isolated-world content scripts. `API_BASE_URL` is set
  to `https://api.eafc.live/api/v2` - confirmed live in production
  (`GET https://api.eafc.live/api/v2/companion/card/117672259` returns
  valid companion JSON). Note this is *not* the same host
  `frontend/.env`'s `VITE_API_URL` records (`api.futhub.co.uk`) - that
  env var still points at the pre-migration domain; see
  `backend/main.py`'s `ALLOWED_ORIGIN_REGEX` comment on the
  futhub.co.uk -> eafc.live move.

## Permissions

- `host_permissions: ["https://api.eafc.live/*"]` - needed only so
  the background service worker's fetch to the companion API bypasses
  CORS (see "How it works" step 5). No EA host is listed: the extension
  never issues its own request to EA - it only wraps `fetch`/`XHR`
  calls the Web App's own JS already makes, inside the page it's already
  running on, which needs no separate host permission.
- `content_scripts[].matches: ["https://*.ea.com/*ultimate-team*web-app*"]`
  - confirmed against a live session: the real Web App URL is
    `https://ea.com/<locale>/ea-sports-fc/ultimate-team/web-app/` (e.g.
    `.../fr-fr/...`) - **no `www.`**. An earlier version of this
    manifest matched only `https://www.ea.com/*`, which meant the
    content script never injected at all on the real site (no error, no
    console output, no overlay - it simply never ran). `*.ea.com` in a
    Chrome match pattern covers both the bare domain and any subdomain,
    so this covers `ea.com` and `www.ea.com` alike regardless of locale.
- `background.service_worker` requires no separate `permissions` entry.

## Assumptions still needing manual verification

This was built without access to a live, authenticated EA FC 26 Web App
session. Do not treat the EA response schema as verified until it has
actually been run against one. Specifically still open:

- The exact response envelope (`auctionInfo` array vs. something else)
  for a search request - see `card-detector.js`'s header for the assumed
  shape, sourced from public community reference implementations, not a
  response captured in this environment.
- That `itemData.resourceId` (or `definitionId`) is present and equal to
  FUT.GG's `source_card_id` for the same card, for at least a few known
  cards (special and non-special).
- Whether a background-worker fetch to `api.eafc.live` actually succeeds
  from inside the installed extension (the CORS-bypass reasoning in "How
  it works" step 5 is standard MV3 behaviour but has not been exercised
  end-to-end from an installed extension, as opposed to a plain `curl`,
  which has confirmed the endpoint itself is live).
- Whether EA ever omits `resourceId` while still providing
  `definitionId` (the fallback path) on any endpoint version.

If the real response shape differs, `card-detector.js` is written
defensively (returns an empty list rather than throwing) but the panel
simply won't appear until the parsing rules are adjusted to match.

## Testing

Pure logic (`card-detector.js`, `card-grouping.js`, `market-calc.js`,
`intelligence-cache.js`) has no DOM/network/`chrome.*` dependency and is
tested directly under Node:

```
npm test
```

## Explicitly out of scope for V1

Autobuying, bidding, listing, relisting, sniping, automatic market
scanning/polling, SBC automation, multi-card Trade Finder scanning, coin
or account management, and anything that modifies the user's EA club.
