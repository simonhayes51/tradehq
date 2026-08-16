// src/config.js
//
// Static configuration consumed by the background service worker
// (src/background.js, via importScripts - the only place that actually
// talks to the EAFC Live backend, see that file's own header for why)
// and, for reference, by the isolated-world content scripts.
//
// Nothing in this file is a secret and nothing here ever travels back
// to EA - it only controls where THIS extension's background worker
// talks to (the EAFC Live backend) and how long a response is cached
// for. See src/debug-flag.js for the separate (and separately-loaded)
// debug-logging toggle.
globalThis.EAFC_LIVE_CONFIG = {
  // Base URL of the EAFC Live backend's v2 API
  // (backend/app/routers/v2/companion.py mounts /companion/card/{id}
  // under this prefix). Confirmed against backend/frontend/.env's
  // VITE_API_URL, the only production API host recorded in this repo -
  // verify this still matches the actually-deployed backend before
  // relying on it (see the extension README's live-test checklist).
  API_BASE_URL: "https://api.futhub.co.uk/api/v2",

  // How long a SUCCESSFUL companion/card/{id} response is reused before
  // re-fetching. Keeps a Transfer Market search returning 20+ auctions
  // for the same card from triggering 20+ backend calls.
  SUCCESS_CACHE_TTL_MS: 5 * 60 * 1000,

  // How long a definitive 404 ("we have never seen this card") is
  // negative-cached. Deliberately much shorter than the success TTL -
  // it's a real fact about our catalogue, not a recommendation that
  // needs a 5-minute shelf life. A transient failure (network error,
  // 5xx, timeout) is NEVER cached at all - see
  // src/intelligence-cache.js's own docstring - so it isn't listed here.
  NOT_FOUND_CACHE_TTL_MS: 60 * 1000,

  // Network timeout for the companion API call, so a slow/unreachable
  // backend can never hang the overlay or the page.
  REQUEST_TIMEOUT_MS: 6000,
};
