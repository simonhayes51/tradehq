// src/eafc-api.js
// Runs in the content script's ISOLATED world.
//
// Thin messaging shim to src/background.js, which is the ONLY place
// this extension actually calls the EAFC.Live backend - see that file's
// header for why the fetch itself lives there and not here (content-
// script fetches are not a reliable way to bypass CORS the way an
// extension page's fetch is).
//
// SECURITY: this file carries nothing but a numeric card id to the
// background worker and back - no EA session value is read, held, or
// forwarded here, same as before.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }

  /**
   * Resolves to a structured result - never throws, never a bare
   * intelligence object - so callers can distinguish a genuine "this
   * card doesn't exist" from a transient failure that's worth retrying:
   *
   *   { status: "success",       data: <companion intelligence object> }
   *   { status: "not_found",     data: null }  // backend 404
   *   { status: "server_error",  data: null }  // backend 5xx / unexpected status
   *   { status: "network_error", data: null }  // unreachable / extension messaging failed
   *   { status: "timeout",       data: null }  // exceeded REQUEST_TIMEOUT_MS
   */
  function getCardIntelligence(sourceCardId) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(
          { type: "EAFC_LIVE_GET_CARD_INTELLIGENCE", sourceCardId },
          (response) => {
            if (chrome.runtime.lastError) {
              // Background worker not ready / extension reloaded / etc.
              log("messaging to background worker failed:", chrome.runtime.lastError.message);
              resolve({ status: "network_error", data: null });
              return;
            }
            resolve(response || { status: "network_error", data: null });
          }
        );
      } catch (err) {
        log("sendMessage threw:", err && err.message);
        resolve({ status: "network_error", data: null });
      }
    });
  }

  window.EAFCLive = window.EAFCLive || {};
  window.EAFCLive.getCardIntelligence = getCardIntelligence;
})();
