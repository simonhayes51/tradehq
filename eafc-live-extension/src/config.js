// src/config.js
//
// Static configuration for the EAFC Live Companion extension. Loaded
// before every other content-script file (see manifest.json's ordering)
// so `window.EAFC_LIVE_CONFIG` is available to all of them.
//
// Nothing in this file is a secret and nothing here ever travels back to
// EA - it only controls where THIS extension talks to (the EAFC Live
// backend) and how noisy its own logging is.
(function () {
  window.EAFC_LIVE_CONFIG = {
    // Base URL of the EAFC Live backend's companion API
    // (backend/app/routers/v2/companion.py). Override by editing this
    // file for a local backend during development.
    API_BASE_URL: "https://api.tradehq.app/api/v2",

    // Milliseconds a companion/card/{id} response is reused for before
    // re-fetching - see src/eafc-api.js. Keeps the extension from
    // hitting the backend once per auction row in a search result.
    CACHE_TTL_MS: 5 * 60 * 1000,

    // Network timeout for the companion API call, so a slow/unreachable
    // backend can never hang the overlay or the page.
    REQUEST_TIMEOUT_MS: 6000,

    // Verbose [EAFC Live] console logging. Flip to true while
    // developing; leave false for anything resembling production use.
    // Never gated on this: the rule that EA session values are never
    // logged (see eafc-api.js / page-bridge.js) - that holds regardless
    // of DEBUG.
    DEBUG: false,
  };
})();
