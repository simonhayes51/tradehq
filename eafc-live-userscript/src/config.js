// src/config.js
//
// Static configuration. Same values as eafc-live-extension/src/config.js
// (see that file's header for why these numbers were chosen) - kept
// identical on purpose so the userscript and Chrome extension behave the
// same way against the same backend.
export const CONFIG = {
  // Base URL of the EAFC.Live backend's v2 API
  // (backend/app/routers/v2/companion.py mounts /companion/card/{id}
  // under this prefix).
  API_BASE_URL: "https://api.eafc.live/api/v2",

  // How long a SUCCESSFUL companion/card/{id} response is reused before
  // re-fetching. Keeps a Transfer Market search returning 20+ auctions
  // for the same card from triggering 20+ backend calls.
  SUCCESS_CACHE_TTL_MS: 5 * 60 * 1000,

  // How long a definitive 404 ("we have never seen this card") is
  // negative-cached. A transient failure (network error, 5xx, timeout)
  // is NEVER cached at all - see intelligence-cache.js.
  NOT_FOUND_CACHE_TTL_MS: 60 * 1000,

  // Network timeout for the companion API call, so a slow/unreachable
  // backend can never hang the overlay or the page. This is a single
  // fast per-card lookup, so a short timeout is correct here.
  REQUEST_TIMEOUT_MS: 6000,

  // Network timeout for GET /opportunities specifically. That endpoint
  // is backed by the same segmented candidate scan + evaluate_card()
  // pass documented across backend/app/routers/v2/futgg_market.py - on a
  // cold in-process cache it can legitimately take 10-20+ seconds, the
  // same reasoning behind the website frontend's own
  // SCAN_REQUEST_OPTS vs REQUEST_OPTS split (see
  // frontend/src/v2/hooks/useFutggMarket.js). Reusing REQUEST_TIMEOUT_MS
  // for this endpoint made the nav-tab panel time out and report "Could
  // not load opportunities right now" well before the backend was
  // actually done - the panel just never showed trades.
  SCAN_REQUEST_TIMEOUT_MS: 20000,
};
