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
eafc-live.user.js   (bundled from src/, single execution context)
    ├── market-observer.js   — hooks window.fetch / XMLHttpRequest,
    │                          reads /transfermarket responses EA's own
    │                          page already requested
    ├── card-detector.js     — exact card identity (resourceId, falling
    │                          back to definitionId; NEVER maskedDefId
    │                          alone) + sanitized auction extraction
    ├── api-client.js        — GM_xmlhttpRequest-based client for
    │                          GET https://api.eafc.live/api/v2/companion/card/{id}
    ├── intelligence-cache.js— success/not_found/never-cache-failures
    │                          caching policy in front of api-client.js
    ├── market-calc.js       — combines live EA auctions with backend
    │                          intelligence into the overlay's numbers
    ├── overlay.js            — the small floating panel (Shadow DOM)
    └── lifecycle.js          — idempotent init guard for SPA re-entry
```

Unlike the Chrome extension (`eafc-live-extension/`), this all runs in
**one JS execution context** — a userscript with `@inject-into page`
runs directly in the page's own realm, so there is no MAIN-world /
isolated-world split, no `CustomEvent` bridge, and no background service
worker. `GM_xmlhttpRequest` (declared via `@grant`) plays the same role
the extension's privileged background-worker fetch did: it is exempt
from the page's own CORS policy, so it can reach `api.eafc.live`
directly without the backend having to loosen its CORS allowlist for
the EA page's origin.

Most of the pure logic (`card-detector.js`, `market-calc.js`,
`intelligence-cache.js`) is ported near-verbatim from
`eafc-live-extension/src/` — same functions, same behaviour, only the
UMD/browser-extension wrapper removed since it isn't needed in a single
bundled ES module.

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

## Hosting requirement

`@downloadURL` / `@updateURL` both point at
`https://eafc.live/companion/eafc-live.user.js`. For userscript-manager
auto-updates to work, `dist/eafc-live.user.js` needs to be served at
that exact URL (same file, same version-bump-on-change convention) —
this repository does not currently include the deploy step that
publishes it there; that's an infrastructure task outside this
directory's scope.

## Installation

### Desktop (Chrome, Firefox, Edge)

1. Install **Tampermonkey** or **Violentmonkey** from your browser's
   extension store.
2. Visit `https://eafc.live/companion/eafc-live.user.js` (or the
   `INSTALL COMPANION` button on `https://eafc.live/companion`) — the
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
   `https://eafc.live/companion/eafc-live.user.js` as a new script, or
   use the app's "add from URL" install flow if it detects the
   `.user.js` link directly from Safari.
4. Open the EA FC 26 Web App in Safari.

**Not yet verified against a real iOS device/session in this project** —
the compatibility claim above is based on the Userscripts app's own
published feature support (`GM.xmlHttpRequest`, confirmed as of its
v4.7.1 release), not a live test performed here.

### Android

1. Install **Firefox for Android** from the Play Store (not Chrome —
   Chrome for Android does not support browser extensions/userscript
   managers at all).
2. In Firefox for Android, install **Tampermonkey** from Firefox's
   Add-ons page (Tampermonkey ships an official Firefox-for-Android
   build, and added `GM_xmlhttpRequest` proxy support for it).
3. Visit `https://eafc.live/companion/eafc-live.user.js` in Firefox —
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

## What still needs a real authenticated EA session to verify

- Whether `/ut/game/fc26/transfermarket` responses (and the
  `resourceId`/`definitionId`/`maskedDefId`/`auctionInfo` shape assumed
  by `card-detector.js`) still match what a live search actually
  returns — this was last confirmed working end-to-end against a real
  session for the Chrome extension (see `eafc-live-extension/README.md`
  for that verification), and the parsing logic is unchanged here, but
  the userscript's own fetch/XHR hook and GM_xmlhttpRequest path have
  not yet been exercised against a live EA session.
- Whether `@inject-into page` behaves identically to the Chrome
  extension's MAIN-world injection for observing the page's real
  `window.fetch`/`XMLHttpRequest` (rather than a sandboxed copy) on each
  of Tampermonkey, Violentmonkey, and Userscripts (iOS) — this differs
  by userscript manager and was not independently tested here.
- The iPhone and Android install/behaviour claims above, which are
  based on each project's own published capabilities, not a live device
  test performed in this environment.
