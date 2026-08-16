# EAFC Live Companion (V1)

A **read-only** Chrome extension that runs inside the EA SPORTS FC 26 Web
App and shows whether the live Transfer Market confirms an existing
EAFC Live trading recommendation for the exact card you're viewing.

It never bids, buys, lists, relists, snipes, or takes any write action
against EA. It only observes responses the Web App already receives and
displays a small local overlay.

## How it works

1. **`src/ea-session.js`** / **`src/page-bridge.js`** run in the page's
   `MAIN` world and wrap `window.fetch` / `XMLHttpRequest` to observe
   (never modify) responses from `/ut/game/fc26`, specifically
   `/transfermarket`. They dispatch a `CustomEvent` on `window` with the
   parsed response body. No EA session credential (cookie, `X-UT-SID`,
   `X-UT-PHISHING-TOKEN`, password) is ever read, logged, or forwarded -
   these files only look at URLs and response bodies, never headers.
2. **`src/card-detector.js`** (pure logic) turns a Transfer Market
   response into exact card identity: `resourceId` (falling back to
   `definitionId`), **never** `maskedDefId` alone, since that identifies
   the underlying player and collides across special versions.
3. **`src/market-observer.js`** listens for the observed-response event,
   runs card-detector over it, and notifies `content.js`.
4. **`src/eafc-api.js`** calls `GET /api/v2/companion/card/{source_card_id}`
   on the EAFC Live backend - a plain unauthenticated GET carrying only
   the numeric card id, cached client-side per id for
   `EAFC_LIVE_CONFIG.CACHE_TTL_MS` (default 5 minutes).
5. **`src/market-calc.js`** (pure logic) combines the backend
   intelligence with the live EA auctions: cheapest BIN, listing counts
   under target/fair value, nearest expiry, after-tax profit at the
   cheapest listing. It never invents a BUY signal - a live market with
   no backend intelligence always resolves to "no confirmation".
6. **`src/overlay.js`** renders a small panel (closed Shadow DOM, its own
   styling) in the bottom-right corner: `OPPORTUNITY CONFIRMED` or
   `NO LIVE LISTING BELOW TARGET`.
7. **`src/content.js`** wires 3-6 together.

## Loading the extension

1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `eafc-live-extension/` folder.
3. Open the EA FC 26 Web App and search the Transfer Market normally.

## Configuration

Edit `src/config.js`:

- `API_BASE_URL` - the EAFC Live backend's `/api/v2` base URL.
- `DEBUG` - set `true` for `[EAFC Live]` console logging (never logs
  session credentials, regardless of this flag).

## Assumptions still needing manual verification

This was built without access to a live, authenticated EA FC 26 Web App
session. The Transfer Market response shape in `card-detector.js`'s
header comment is based on publicly documented community reference
implementations, not a response captured from this environment. Before
relying on this in production, confirm against a real session:

- The exact response envelope (`auctionInfo` array vs. something else)
  for a search request.
- That `itemData.resourceId` (or `definitionId`) is present and equal to
  FUT.GG's `source_card_id` for the same card, for at least a few known
  cards (special and non-special).
- The exact `/ut/game/fc26` path segment used for a search request vs. a
  watchlist/purchased-items read (both may be worth observing later, but
  V1 only targets `/transfermarket`).
- Whether EA ever omits `resourceId` while still providing
  `definitionId` (the fallback path) on any endpoint version.

If the real response shape differs, `card-detector.js` is written
defensively (returns an empty list rather than throwing) but the panel
simply won't appear until the parsing rules are adjusted to match.

## Testing

Pure logic (`card-detector.js`, `market-calc.js`) has no DOM/network
dependency and is tested directly under Node:

```
npm test
```

## Explicitly out of scope for V1

Autobuying, bidding, listing, relisting, sniping, automatic market
scanning/polling, SBC automation, multi-card Trade Finder scanning, coin
or account management, and anything that modifies the user's EA club.
