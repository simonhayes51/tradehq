// src/eafc-api.js
//
// Talks to the EAFC Live backend's companion API
// (GET /api/v2/companion/card/{source_card_id}) - and nowhere else.
//
// SECURITY: this file must never read or forward any EA session value
// (X-UT-SID, X-UT-PHISHING-TOKEN, EA cookies, EA password). The request
// it makes carries nothing but a numeric card id in the URL - no
// headers, no body, no credentials - by construction, not by care taken
// at each call site. ea-session.js / page-bridge.js keep EA's session
// values inside the page/content-script boundary and never pass them
// into this module.
//
// Caching: short-lived, keyed by exact source_card_id (per the
// milestone's "caching and request discipline" requirement) - a
// transfer-market search page can return 20+ auctions for the same
// card, and without this each one would trigger its own backend call.
(function () {
  "use strict";

  const cfg = window.EAFC_LIVE_CONFIG || {};
  const API_BASE_URL = cfg.API_BASE_URL || "https://api.tradehq.app/api/v2";
  const CACHE_TTL_MS = cfg.CACHE_TTL_MS || 5 * 60 * 1000;
  const REQUEST_TIMEOUT_MS = cfg.REQUEST_TIMEOUT_MS || 6000;

  function log(...args) {
    if (cfg.DEBUG) console.debug("[EAFC Live]", ...args);
  }

  // sourceCardId -> { expiresAt, promise|value }
  const cache = new Map();

  function cachedEntryIsFresh(entry) {
    return !!entry && entry.expiresAt > Date.now();
  }

  async function fetchWithTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, {
        method: "GET",
        signal: controller.signal,
        // Explicitly no credentials: this is a plain public GET against
        // our own backend, never the EA session.
        credentials: "omit",
        headers: { Accept: "application/json" },
      });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Fetch (or reuse a cached) companion intelligence object for one
   * exact card id. Resolves to `null` - never throws - on any failure:
   * backend unreachable, non-2xx, malformed JSON, or an unknown card
   * (404). This is the boundary where "EAFC Live backend unavailable"
   * degrades to "no intelligence for this card" rather than an
   * exception that could ever interrupt the host page.
   */
  async function getCardIntelligence(sourceCardId) {
    const id = Number(sourceCardId);
    if (!Number.isFinite(id) || id <= 0) return null;

    const cached = cache.get(id);
    if (cachedEntryIsFresh(cached)) {
      log("cache hit for card", id);
      return cached.promise;
    }

    const promise = (async () => {
      try {
        const url = `${API_BASE_URL}/companion/card/${id}`;
        const res = await fetchWithTimeout(url, REQUEST_TIMEOUT_MS);
        if (res.status === 404) {
          log("no intelligence available for card", id);
          return null;
        }
        if (!res.ok) {
          log("companion API returned", res.status, "for card", id);
          return null;
        }
        const data = await res.json();
        log("intelligence loaded for card", id, data && data.signal);
        return data;
      } catch (err) {
        log("companion API request failed:", err && err.message);
        return null;
      }
    })();

    cache.set(id, { expiresAt: Date.now() + CACHE_TTL_MS, promise });
    return promise;
  }

  window.EAFCLive = window.EAFCLive || {};
  window.EAFCLive.getCardIntelligence = getCardIntelligence;
})();
