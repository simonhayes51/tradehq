// src/background.js
// MV3 service worker - the ONLY place this extension makes a network
// request to the EAFC Live backend.
//
// WHY THE FETCH LIVES HERE AND NOT IN THE CONTENT SCRIPT
// -------------------------------------------------------------------
// A content script's fetch() runs with the WEB PAGE's origin for CORS
// purposes, not the extension's - host_permissions do not reliably
// exempt it the way they do for an extension page (popup/options/
// background). Doing the fetch from a content script would make success
// depend on undocumented, version-specific Chrome behaviour, and if it
// silently failed the symptom would be exactly the "clean 200 in the
// server logs, nothing reaches the page" CORS failure mode main.py's own
// ALLOWED_ORIGIN_REGEX comment describes for the main TradeHQ frontend -
// which is also why loosening backend CORS to accept the page's own
// origin was rejected as a fix here (that would weaken the backend's
// origin allowlist for every caller, not just this extension).
//
// A background service worker's fetch, by contrast, is unambiguously an
// extension-privileged request: with `https://api.eafc.live/*` in
// manifest.json's host_permissions, it is exempt from CORS entirely and
// needs no cooperation from the backend's CORS configuration at all.
// Content scripts ask this worker for intelligence via
// chrome.runtime.sendMessage (see src/eafc-api.js) instead of fetching
// directly.
//
// SECURITY: exactly like src/eafc-api.js before it, this file sends
// nothing but a numeric card id in the URL - no headers, no body, no EA
// session credential. It has no way to read anything from the EA Web
// App page (service workers have no DOM/window access at all) - the
// only input it accepts is the sourceCardId a content script asks about.
importScripts("debug-flag.js", "config.js", "intelligence-cache.js");

(function () {
  "use strict";

  const cfg = globalThis.EAFC_LIVE_CONFIG || {};
  const API_BASE_URL = cfg.API_BASE_URL || "https://api.eafc.live/api/v2";
  const REQUEST_TIMEOUT_MS = cfg.REQUEST_TIMEOUT_MS || 6000;

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC Live:bg]", ...args);
  }

  async function fetchIntelligence(sourceCardId) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const url = `${API_BASE_URL}/companion/card/${sourceCardId}`;
      const res = await fetch(url, {
        method: "GET",
        signal: controller.signal,
        // Explicitly no credentials: this is a plain public GET against
        // our own backend, never the EA session (which this worker has
        // no access to regardless).
        credentials: "omit",
        headers: { Accept: "application/json" },
      });

      if (res.status === 404) {
        log("no intelligence available for card", sourceCardId);
        return { status: "not_found", data: null };
      }
      if (res.status >= 500) {
        log("companion API server error", res.status, "for card", sourceCardId);
        return { status: "server_error", data: null };
      }
      if (!res.ok) {
        // Any other non-2xx (e.g. an unexpected 4xx) - treated the same
        // as a server-side problem: not a confirmed "card doesn't
        // exist", so still retryable rather than negative-cached.
        log("companion API unexpected status", res.status, "for card", sourceCardId);
        return { status: "server_error", data: null };
      }

      const data = await res.json();
      log("intelligence loaded for card", sourceCardId, data && data.signal);
      return { status: "success", data };
    } catch (err) {
      if (controller.signal.aborted) {
        log("companion API request timed out for card", sourceCardId);
        return { status: "timeout", data: null };
      }
      log("companion API request failed for card", sourceCardId, ":", err && err.message);
      return { status: "network_error", data: null };
    } finally {
      clearTimeout(timer);
    }
  }

  const cache = globalThis.EAFCLive.createIntelligenceCache(fetchIntelligence, {
    successTtlMs: cfg.SUCCESS_CACHE_TTL_MS,
    notFoundTtlMs: cfg.NOT_FOUND_CACHE_TTL_MS,
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== "EAFC_LIVE_GET_CARD_INTELLIGENCE") return false;
    cache
      .get(message.sourceCardId)
      .then(sendResponse)
      .catch(() => sendResponse({ status: "network_error", data: null }));
    return true; // keep the message channel open for the async response
  });

  log("background service worker ready");
})();
