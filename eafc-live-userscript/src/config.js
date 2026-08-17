// src/config.js
//
// Static configuration. Same values as eafc-live-extension/src/config.js
// (see that file's header for why these numbers were chosen) - kept
// identical on purpose so the userscript and Chrome extension behave the
// same way against the same backend.
export const CONFIG = {
  // Base URL of the EAFC Live backend's v2 API
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
  // backend can never hang the overlay or the page.
  REQUEST_TIMEOUT_MS: 6000,
};
