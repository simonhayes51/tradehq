# EAFC Live Companion — userscript

Read-only EAFC Live trading-intelligence overlay for the official EA
SPORTS FC 26 Web App Transfer Market, delivered as a **userscript**
instead of a browser extension. Install once with a userscript manager;
it then runs automatically every time you open the real EA Web App.
Strictly observation-only — see "Out of scope" below.

This replaces the earlier `eafc-live-mobile/` (Capacitor native-shell)
approach, which has been abandoned in favor of this delivery model: no
Xcode, no Android Studio, no Mac, no custom native app required.

## How it works

```
eafc-live.user.js        (bundled from src/)
    ├── page-hook.js        — bundled SEPARATELY, injected as a literal
    │                         <script> tag at runtime so it always runs
    │                         in the page's TRUE realm (see below).
    │                         Hooks window.fetch/XMLHttpRequest, reads
    │                         /transfermarket responses EA's own page
    │                         already requested, sanitizes, and
    │                         dispatches a CustomEvent.
    ├── market-observer.js  — runs in the userscript manager's default
    │                         context; injects page-hook.js and listens
    │                         for its event
    ├── card-detector.js    — exact card identity (resourceId, falling
    │                         back to definitionId; NEVER maskedDefId
    │                         alone) + sanitized auction extraction
    │                         (used by BOTH bundles above)
    ├── api-client.js       — GM_xmlhttpRequest/GM.xmlHttpRequest client
    │                         for GET .../companion/card/{id}
    ├── intelligence-cache.js — success/not_found/never-cache-failures
    │                         caching policy in front of api-client.js
    ├── market-calc.js      — combines live EA auctions with backend
    │                         intelligence into the overlay's numbers
    ├── overlay.js           — the single-card floating panel (Shadow DOM)
    ├── list-badges.js       — per-row BUY/AVOID markers for a
    │                         multi-card search (see below)
    └── lifecycle.js         — idempotent init guard for SPA re-entry
```

**Two execution contexts, not one.** V1 ran the entire userscript with
`@inject-into page`, on the assumption that this was needed both to
observe `window.fetch`/`XHR` reliably AND to reach
`GM_xmlhttpRequest`/`GM.xmlHttpRequest`. Confirmed live on a real iOS
session: only the first half was true - at least one real, currently-
used manager (the "Userscripts" app) does not bridge either GM API into
a script actually running in page context, only into its own default
context. So responsibility is split: `page-hook.js` is bundled to its
own IIFE and injected as a plain `<script>` tag (always runs in the
page's true realm, regardless of manager, since it's just DOM
manipulation - the same as any page's own inline script), while
everything else runs in the manager's default context, where GM access
is reliable. The two talk over one `CustomEvent` on `window`, carrying
only the sanitized auction fields - the same MAIN-world/isolated-world
security boundary `eafc-live-extension/` used, for the identical reason.

`GM_xmlhttpRequest`/`GM.xmlHttpRequest` (declared via `@grant`) play the
same role the Chrome extension's privileged background-worker fetch
did: exempt from the page's own CORS policy, so they can reach
`api.eafc.live` directly without the backend having to loosen its CORS
allowlist for the EA page's origin.

Most of the pure logic (`card-detector.js`, `market-calc.js`,
`intelligence-cache.js`) is ported near-verbatim from
`eafc-live-extension/src/` — same functions, same behaviour, only the
UMD/browser-extension wrapper removed since it isn't needed in a
bundled ES module.

## Per-row badges (multi-card search)

`overlay.js`'s floating panel only ever describes ONE card - it can't
say anything useful when a search returns many different players at
once (an unfiltered Transfer Market search). `list-badges.js` adds a
small floating "✓ BUY" / "⚠ AVOID" marker positioned over each row's own
"Buy Now" price, independent of the summary panel, driven by that row's
OWN price against its OWN card's backend recommendation - never an
aggregate. A row with a `watch`/`insufficient_data` signal, or a `buy`
signal whose price doesn't clear the target, stays unbadged rather than
showing a neutral/uncertain marker - same "never manufacture a signal"
principle as the overlay. Tapping a badge opens the same single-card
overlay panel for that card.

**This is the one part of the userscript that has to touch EA's live
DOM** (every other file only ever reasons from parsed network
responses) - it text-matches each auction's formatted price to find
where EA rendered it on screen, which is inherently a best-effort match
against markup this project has no other visibility into. To keep a
wrong match harmless, badges are positioned `fixed` elements anchored to
on-screen coordinates, never inserted into EA's own row - a bad match
degrades to "a badge floats in the wrong spot", never to broken EA
layout or click handling. `findPriceAnchor()` in `list-badges.js` is the
function to revisit if badges are missing or misplaced on a real device.

## Building

```bash
cd eafc-live-userscript
npm install
npm run build   # -> dist/eafc-live.user.js
npm test        # 37 tests, node's built-in test runner
```

`dist/eafc-live.user.js` is **not minified on purpose** — the user
asked that the distributed script stay auditable and readable, unlike
`autosbc.user.js`'s obfuscated body. Do not add a minify step to this
build without an explicit request.

## Hosting

`@downloadURL` / `@updateURL` both point at
`https://app.eafc.live/companion/eafc-live.user.js`. The built file is
checked into `frontend/public/companion/eafc-live.user.js` — Vite
serves everything under `frontend/public/` verbatim from the root of its
build output, so it ships automatically with every `app.eafc.live`
deploy, the same as `frontend/public/manifest.json` or
`frontend/public/sw.js` already do.

**This copy is not regenerated automatically.** After changing anything
under `eafc-live-userscript/src/`, bump `version` in
`eafc-live-userscript/src/metadata.js` (userscript managers key
auto-update off this), then:

```bash
cd eafc-live-userscript
npm run build
cp dist/eafc-live.user.js ../frontend/public/companion/eafc-live.user.js
```

and commit both the source change and the updated copy in the same PR.

## Installation

### Desktop (Chrome, Firefox, Edge)

1. Install **Tampermonkey** or **Violentmonkey** from your browser's
   extension store.
2. Visit `https://app.eafc.live/companion/eafc-live.user.js` (or the
   `INSTALL COMPANION` button on `https://app.eafc.live/companion`) — the
   userscript manager intercepts the `.user.js` URL and shows an install
   prompt.
3. Confirm the install. Open the EA FC 26 Web App normally
   (`https://www.ea.com/ea-sports-fc/ultimate-team/web-app/`) — the
   "EAFC LIVE" panel should appear automatically once you view a card in
   the Transfer Market.

This is the same manager/pattern `autosbc.user.js` itself ships with, so
it's a proven, currently-working install path for this exact page.

### iPhone / iPad (Safari)

1. Install **Userscripts** by quoid from the App Store
   (`id1463298887`) — open-source, actively maintained, confirmed to
   support `GM.xmlHttpRequest`. Requires iOS 15.1+.
2. Open Settings → Safari → Extensions → Userscripts, and turn it on
   (Safari extensions must be explicitly enabled here).
3. In the Userscripts app (or via Safari's extension settings), add
   `https://app.eafc.live/companion/eafc-live.user.js` as a new script, or
   use the app's "add from URL" install flow if it detects the
   `.user.js` link directly from Safari.
4. Open the EA FC 26 Web App in Safari.

**Confirmed working end-to-end on a real iOS session** (Userscripts app,
Safari, live EA FC 26 Web App Transfer Market) as of v1.1.0 - exact card
identity, live BIN/listings, and the correct verdict all rendered
correctly. Getting there took two real fixes discovered only through
live testing: v1.0.1 added a fallback to the namespaced `GM.xmlHttpRequest`
API, and v1.1.0 (see `src/page-hook.js`) stopped running the whole
script with `@inject-into page`, since this manager does not bridge
EITHER GM API into a script actually running in page context - only its
default context has that access. The fetch/XHR observation itself still
needs true page-context execution, so it now runs via a separately
bundled, injected `<script>` tag instead of the userscript's own
top-level context. If you hit anything unexpected on iOS, it's most
likely version drift - confirm the installed script reports `1.1.0`+
before assuming a new bug.

### Android

1. Install **Firefox for Android** from the Play Store (not Chrome —
   Chrome for Android does not support browser extensions/userscript
   managers at all).
2. In Firefox for Android, install **Tampermonkey** from Firefox's
   Add-ons page (Tampermonkey ships an official Firefox-for-Android
   build, and added `GM_xmlhttpRequest` proxy support for it).
3. Visit `https://app.eafc.live/companion/eafc-live.user.js` in Firefox —
   Tampermonkey intercepts it and offers to install.
4. Open the EA FC 26 Web App in Firefox for Android.

**Kiwi Browser is no longer a valid recommendation** — it was
officially discontinued and pulled from the Play Store; its last build
is a manually-sideloaded APK frozen on old Manifest V2 support with no
further updates. Do not suggest it as a primary Android path.

**Not yet verified against a real Android device/session in this
project** — based on Tampermonkey's own published Firefox-for-Android
support, not a live test performed here.

## Security

- Only `{ sourceCardId, tradeId, rating, buyNowPrice, currentBid,
  startingBid, expires, tradeState }` is ever extracted per auction —
  never the raw response body, never `maskedDefId`, never a request
  header.
- Never reads cookies, `X-UT-SID`, `X-UT-PHISHING-TOKEN`, an EA
  password, or any other session credential.
- `GM_xmlhttpRequest` calls to `api.eafc.live` carry only the numeric
  `sourceCardId` in the URL — no headers beyond `Accept:
  application/json`, no EA credentials.
- Never proxies EA — `LAUNCH FC WEB APP` on the launcher page links
  straight to `ea.com`.

## Out of scope (strictly unimplemented)

Autobuy, bidding, listing, relisting, sniping, automated searching, SBC
submission, moving club items, duplicate recycling, market polling, or
any other automated trading action. EAFC Live Companion remains
passive/read-only. `autosbc.user.js` was used only as an architectural
reference for real, working `@match`/`@grant`/`@inject-into`/
`@downloadURL` metadata — its (obfuscated) SBC automation logic was
never read or copied.

## What's confirmed vs. still open

**Confirmed on a real iOS session (Userscripts app), v1.1.0+:**
`/ut/game/fc26/transfermarket` response parsing (`card-detector.js`),
exact-card identity, the injected page-hook's fetch/XHR observation, the
GM_xmlhttpRequest/GM.xmlHttpRequest fallback in `api-client.js`, and the
overlay end-to-end against a live Mbappé TOTY card.

**Still open:**
- Desktop (Tampermonkey/Violentmonkey) and Android (Firefox +
  Tampermonkey) installs are still based on each project's own
  published capabilities, not a live device test performed in this
  project - the iOS case above is the only one actually exercised
  end-to-end so far. Given how many real, manager-specific surprises iOS
  turned up (GM API availability, `@inject-into page` behavior), treat
  desktop/Android as unverified until someone actually tests them, not
  as "probably fine because iOS worked."
- Whether the page-hook's injected `<script>` tag technique behaves
  identically across Tampermonkey/Violentmonkey on desktop - expected to
  work (it's plain DOM manipulation, not a GM feature), but not
  independently confirmed there yet.
